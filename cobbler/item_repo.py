"""
A Cobbler repesentation of a yum repo.

Copyright 2006-2009, Red Hat, Inc
Michael DeHaan <mdehaan@redhat.com>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
02110-1301  USA
"""

import utils
import item
from cexceptions import *
from utils import _

import os
HAS_YUM = True
try:
    import yum
except:
    HAS_YUM = False

VALID_REPO_BREEDS = ( "rsync", "rhn", "yum", "apt" )

# this datastructure is described in great detail in item_distro.py -- read the comments there.

FIELDS = [
  ["arch","",0,"Arch",True,"ex: i386, x86_64",['i386','x86_64','ia64','ppc','s390', 'noarch', 'src'],"str"],
  ["breed","",0,"Breed",True,"",VALID_REPO_BREEDS,"str"],
  ["comment","",0,"Comment",True,"Free form text description",0,"str"],
  ["ctime",0,0,"",False,"",0,"float"],
  ["depth",2,0,"",False,"",0,"float"],
  ["keep_updated",True,0,"Keep Updated",True,"Update this repo on next 'cobbler reposync'?",0,"bool"],
  ["mirror",None,0,"Mirror",True,"Address of yum or rsync repo to mirror",0,"str"],
  ["mtime",0,0,"",False,"",0,"float"],
  ["name","",0,"Name",True,"Ex: f10-i386-updates",0,"str"],
  ["owners","SETTINGS:default_ownership",0,"Owners",True,"Owners list for authz_ownership (space delimited)",[],"list"],
  ["parent",None,0,"",False,"",0,"str"],
  ["rpm_list",[],0,"RPM List",True,"Mirror just these RPMs (yum only)",0,"list"],
#  ["os_version","",0,"OS Version",True,"ex: rhel4"],
  ["uid",None,0,"",False,"",0,"str"],
  ["createrepo_flags",'<<inherit>>',0,"Createrepo Flags",True,"Flags to use with createrepo",0,"dict"],
  ["environment",{},0,"Environment Variables",True,"Use these environment variables during commands (key=value, space delimited)",0,"dict"],
  ["mirror_locally",True,0,"Mirror locally",True,"Copy files or just reference the repo externally?",0,"bool"],
  ["priority",99,0,"Priority",True,"Value for yum priorities plugin, if installed",0,"int"],
  ["yumopts",{},0,"Yum Options",True,"Options to write to yum config file",0,"dict"]
]

class Repo(item.Item):

    TYPE_NAME = _("repo")
    COLLECTION_TYPE = "repo"

    def Factory(config,seed_data):
        if seed_data.get( 'breed' ) == "rsync" :
            obj = RsyncRepo(config)
        elif seed_data.get( 'breed' ) == "yum" :
            obj = YumRepo(config)
        elif seed_data.get( 'breed' ) == "rhn" :
            obj = RhnRepo(config)
        elif seed_data.get( 'breed' ) == "apt" :
            obj = AptRepo(config)
        else:
            obj = VoidRepo(config)
        obj.from_datastruct(seed_data)
        return obj
    Factory = staticmethod(Factory)

    breed = None

    def make_clone(self):
        ds = self.to_datastruct()
        cloned = self.Factory(self.config,ds)
        if isinstance(cloned, VoidRepo):
            raise CX("Wrong Repo cloning. Product is a VoidRepo object")
        return cloned

    def get_fields(self):
        return FIELDS

    def _guess_breed(self):
           # backwards compatibility
           if self.mirror.startswith("http://") or self.mirror.startswith("ftp://"):
              self.set_breed("yum")
           elif self.mirror.startswith("rhn://"):
              self.set_breed("rhn")
           else:
              self.set_breed("rsync")

    def set_mirror(self,mirror):
        """
        A repo is (initially, as in right now) is something that can be rsynced.
        reposync/repotrack integration over HTTP might come later.
        """
        self.mirror = mirror
        if self.arch is None or self.arch == "":
           if mirror.find("x86_64") != -1:
              self.set_arch("x86_64")
           elif mirror.find("x86") != -1 or mirror.find("i386") != -1:
              self.set_arch("i386")
           elif mirror.find("ia64") != -1:
              self.set_arch("ia64")
           elif mirror.find("s390") != -1:
              self.set_arch("s390x")
        return True

    def set_keep_updated(self,keep_updated):
        """
    This allows the user to disable updates to a particular repo for whatever reason.
    """
        self.keep_updated = utils.input_boolean(keep_updated)
        return True

    def set_yumopts(self,options,inplace=False):
        """
        Kernel options are a space delimited list,
        like 'a=b c=d e=f g h i=j' or a hash.
        """
        (success, value) = utils.input_string_or_hash(options,allow_multiples=False)
        if not success:
            raise CX(_("invalid yum options"))
        else:
            if inplace:
                for key in value.keys():
                    self.yumopts[key] = value[key]
            else:
                self.yumopts = value
            return True

    def set_environment(self,options,inplace=False):
        """
        Yum can take options from the environment.  This puts them there before
        each reposync.
        """
        (success, value) = utils.input_string_or_hash(options,allow_multiples=False)
        if not success:
            raise CX(_("invalid environment options"))
        else:
            if inplace:
                for key in value.keys():
                    self.environment[key] = value[key]
            else:
                self.environment = value
            return True


    def set_priority(self,priority):
        """
        Set the priority of the repository.  1= highest, 99=default
        Only works if host is using priorities plugin for yum.
        """
        try:
           priority = int(str(priority))
        except:
           raise CX(_("invalid priority level: %s") % priority)
        self.priority = priority
        return True

    def set_rpm_list(self,rpms):
        """
        Rather than mirroring the entire contents of a repository (Fedora Extras, for instance,
        contains games, and we probably don't want those), make it possible to list the packages
        one wants out of those repos, so only those packages + deps can be mirrored.
        """
        self.rpm_list = utils.input_string_or_list(rpms)
        return True

    def set_createrepo_flags(self,createrepo_flags):
        """
        Flags passed to createrepo when it is called.  Common flags to use would be
        -c cache or -g comps.xml to generate group information.
        """
        if createrepo_flags is None:
            createrepo_flags = ""
        self.createrepo_flags = createrepo_flags
        return True

    def set_breed(self,breed):
      if breed != self.breed:
        raise CX(_("Setting breed on %s to an invalid value (%s)") % (self,breed))

    def set_os_version(self,os_version):
        if os_version:
            self.os_version = os_version.lower()
            if not self.breed :
               raise CX(_("cannot set --os-version without setting --breed first"))
            if not self.breed in VALID_REPO_BREEDS:
               raise CX(_("fix --breed first before applying this setting"))
            self.os_version = os_version
        else:
            self.os_version = ""
        return True

    def set_arch(self,arch):
        """
        Override the arch used for reposync
        """
        return utils.set_arch(self,arch,repo=True)

    def set_mirror_locally(self,value):
        self.mirror_locally = utils.input_boolean(value)
        return True

    def get_parent(self):
        """
        currently the Cobbler object space does not support subobjects of this object
        as it is conceptually not useful.
        """
        return None

    def check_if_valid(self):
        if self.name is None:
            raise CX("name is required")
        if self.mirror is None:
            raise CX("Error with repo %s - mirror is required" % (self.name))

    def sync(self, logger):
        raise CX("Repository sync not implemented for %s with type %s" % (self.name,self.breed))

    # NOTE : not used in apt repositories
    def createrepo_walker(self, logger, dirname, fnames):
        """
        Used to run createrepo on a copied Yum mirror.
        """
        if os.path.exists(dirname) or self['breed'] == 'rsync':
            utils.remove_yum_olddata(dirname)

            # add any repo metadata we can use
            mdoptions = []
            if os.path.isfile("%s/.origin/repomd.xml" % (dirname)):
                if not HAS_YUM:
                   utils.die(logger,"yum is required to use this feature")

                rmd = yum.repoMDObject.RepoMD('', "%s/.origin/repomd.xml" % (dirname))
                if rmd.repoData.has_key("group"):
                    groupmdfile = rmd.getData("group").location[1]
                    mdoptions.append("-g %s" % groupmdfile)
                if rmd.repoData.has_key("prestodelta"):
                    # need createrepo >= 0.9.7 to add deltas
                    if utils.check_dist() == "redhat" or utils.check_dist() == "suse":
                        cmd = "/usr/bin/rpmquery --queryformat=%{VERSION} createrepo"
                        createrepo_ver = utils.subprocess_get(logger, cmd)
                        if createrepo_ver >= "0.9.7":
                            mdoptions.append("--deltas")
                        else:
                            logger.error("this repo has presto metadata; you must upgrade createrepo to >= 0.9.7 first and then need to resync the repo through cobbler.")

            blended = utils.blender(self.config.api, False, self)
            flags = blended.get("createrepo_flags","(ERROR: FLAGS)")
            try:
                # BOOKMARK
                cmd = "createrepo %s %s %s" % (" ".join(mdoptions), flags, dirname)
                utils.subprocess_call(logger, cmd)
            except:
                utils.log_exc(logger)
                logger.error("createrepo failed.")
            del fnames[:] # we're in the right place

    # NOTE : not used in apt repositories
    def create_local_file(self, dest_path, output=True):
        """

        Creates Yum config files for use by reposync

        Two uses:
        (A) output=True, Create local files that can be used with yum on provisioned clients to make use of this mirror.
        (B) output=False, Create a temporary file for yum to feed into yum for mirroring
        """
    
        # the output case will generate repo configuration files which are usable
        # for the installed systems.  They need to be made compatible with --server-override
        # which means they are actually templates, which need to be rendered by a cobbler-sync
        # on per profile/system basis.

        if output:
            fname = os.path.join(dest_path,"config.repo")
        else:
            fname = os.path.join(dest_path, "%s.repo" % self.name)
        if not os.path.exists(dest_path):
            utils.mkdir(dest_path)
        config_file = open(fname, "w+")
        config_file.write("[%s]\n" % self.name)
        config_file.write("name=%s\n" % self.name)
        optenabled = False
        optgpgcheck = False
        if output:
            if self.mirror_locally:
                line = "baseurl=http://${server}/cobbler/repo_mirror/%s\n" % (self.name)
            else:
                mstr = self.mirror
                if mstr.startswith("/"):
                    mstr = "file://%s" % mstr
                line = "baseurl=%s\n" % mstr
  
            config_file.write(line)
            # user may have options specific to certain yum plugins
            # add them to the file
            for x in self.yumopts:
                config_file.write("%s=%s\n" % (x, self.yumopts[x]))
                if x == "enabled":
                    optenabled = True
                if x == "gpgcheck":
                    optgpgcheck = True
        else:
            mstr = self.mirror
            if mstr.startswith("/"):
                mstr = "file://%s" % mstr
            line = "baseurl=%s\n" % mstr
            if self.settings.http_port not in (80, '80'):
                http_server = "%s:%s" % (self.settings.server, self.settings.http_port)
            else:
                http_server = self.settings.server
            line = line.replace("@@server@@",http_server)
            config_file.write(line)
        if not optenabled:
            config_file.write("enabled=1\n")
        config_file.write("priority=%s\n" % self.priority)
        # FIXME: potentially might want a way to turn this on/off on a per-repo basis
        if not optgpgcheck:
            config_file.write("gpgcheck=0\n")
        config_file.close()
        return fname 

class VoidRepo ( Repo ) :

    breed = "void"

class RsyncRepo ( Repo ) :

    breed = "rsync"

    def sync(self, logger):

        if not self.mirror_locally:
            utils.die(logger,"rsync:// urls must be mirrored locally, yum cannot access them directly")

        if self.rpm_list != "" and self.rpm_list != []:
            logger.warning("--rpm-list is not supported for rsync'd repositories")

        # FIXME: don't hardcode
        dest_path = os.path.join(self.settings.webdir+"/repo_mirror", self.name)

        spacer = ""
        if not self.mirror.startswith("rsync://") and not self.mirror.startswith("/"):
            spacer = "-e ssh"
        if not self.mirror.endswith("/"):
            self.mirror = "%s/" % self.mirror

        # FIXME: wrapper for subprocess that logs to logger
        cmd = "rsync -rltDv %s --delete --exclude-from=/etc/cobbler/rsync.exclude %s %s" % (spacer, self.mirror, dest_path)
        rc = utils.subprocess_call(logger, cmd)

        if rc !=0:
            utils.die(logger,"cobbler reposync failed")
        os.path.walk(dest_path, self.createrepo_walker, logger)
        self.create_local_file(dest_path)

class YumRepo ( Repo ) :

    breed = "yum"

    def sync(self, logger):

        # warn about not having yum-utils.  We don't want to require it in the package because
        # RHEL4 and RHEL5U0 don't have it.

        if not os.path.exists("/usr/bin/reposync"):
            utils.die(logger,"no /usr/bin/reposync found, please install yum-utils")

        cmd = ""                  # command to run
        has_rpm_list = False      # flag indicating not to pull the whole repo

        # detect cases that require special handling

        if self.rpm_list != "" and self.rpm_list != []:
            has_rpm_list = True

        # create yum config file for use by reposync
        dest_path = os.path.join(self.settings.webdir+"/repo_mirror", self.name)
        temp_path = os.path.join(dest_path, ".origin")

        if not os.path.isdir(temp_path) and self.mirror_locally:
            # FIXME: there's a chance this might break the RHN D/L case
            os.makedirs(temp_path)
         
        # create the config file that yum will use for the copying

        if self.mirror_locally:
            temp_file = self.create_local_file(temp_path, False)

        if not has_rpm_list and self.mirror_locally:
            # if we have not requested only certain RPMs, use reposync
            rflags = self.settings.reposync_flags
            cmd = "/usr/bin/reposync %s --config=%s --repoid=%s --download_path=%s" % (rflags, temp_file, self.name, self.settings.webdir+"/repo_mirror")
            if self.arch != "":
                if self.arch == "x86":
                   self.arch = "i386" # FIX potential arch errors
                if self.arch == "i386":
                   # counter-intuitive, but we want the newish kernels too
                   cmd = "%s -a i686" % (cmd)
                else:
                   cmd = "%s -a %s" % (cmd, self.arch)

        elif self.mirror_locally:

            # create the output directory if it doesn't exist
            if not os.path.exists(dest_path):
               os.makedirs(dest_path)

            use_source = ""
            if self.arch == "src":
                use_source = "--source"
 
            # older yumdownloader sometimes explodes on --resolvedeps
            # if this happens to you, upgrade yum & yum-utils
            extra_flags = self.settings.yumdownloader_flags
            cmd = "/usr/bin/yumdownloader %s %s --disablerepo=* --enablerepo=%s -c %s --destdir=%s %s" % (extra_flags, use_source, self.name, temp_file, dest_path, " ".join(self.rpm_list))

        # now regardless of whether we're doing yumdownloader or reposync
        # or whether the repo was http://, ftp://, or rhn://, execute all queued
        # commands here.  Any failure at any point stops the operation.

        if self.mirror_locally:
            rc = utils.subprocess_call(logger, cmd)
            if rc !=0:
                utils.die(logger,"cobbler reposync failed")

        repodata_path = os.path.join(dest_path, "repodata")

        if not os.path.exists("/usr/bin/wget"):
            utils.die(logger,"no /usr/bin/wget found, please install wget")

        # grab repomd.xml and use it to download any metadata we can use
        cmd2 = "/usr/bin/wget -q %s/repodata/repomd.xml -O %s/repomd.xml" % (self.mirror, temp_path)
        rc = utils.subprocess_call(logger,cmd2)
        if rc == 0:
            # create our repodata directory now, as any extra metadata we're
            # about to download probably lives there
            if not os.path.isdir(repodata_path):
                os.makedirs(repodata_path)
            rmd = yum.repoMDObject.RepoMD('', "%s/repomd.xml" % (temp_path))
            for mdtype in rmd.repoData.keys():
                # don't download metadata files that are created by default
                if mdtype not in ["primary", "primary_db", "filelists", "filelists_db", "other", "other_db"]:
                    mdfile = rmd.getData(mdtype).location[1]
                    cmd3 = "/usr/bin/wget -q %s/%s -O %s/%s" % (self.mirror, mdfile, dest_path, mdfile)
                    utils.subprocess_call(logger,cmd3)
                    if rc !=0:
                        utils.die(logger,"wget failed")

        # now run createrepo to rebuild the index

        if self.mirror_locally:
            os.path.walk(dest_path, self.createrepo_walker, logger)

        # create the config file the hosts will use to access the repository.

        self.create_local_file(dest_path)

class RhnRepo ( Repo ) :

    breed = "rhn"

    def sync(self, logger):

        # FIXME? warn about not having yum-utils.  We don't want to require it in the package because
        # RHEL4 and RHEL5U0 don't have it.

        if not os.path.exists("/usr/bin/reposync"):
            utils.die(logger,"no /usr/bin/reposync found, please install yum-utils")

        cmd = ""                  # command to run
        has_rpm_list = False      # flag indicating not to pull the whole repo

        # detect cases that require special handling

        if self.rpm_list != "" and self.rpm_list != []:
            has_rpm_list = True

        # create yum config file for use by reposync
        # FIXME: don't hardcode
        dest_path = os.path.join(self.settings.webdir+"/repo_mirror", self.name)
        temp_path = os.path.join(dest_path, ".origin")

        if not os.path.isdir(temp_path):
            # FIXME: there's a chance this might break the RHN D/L case
            os.makedirs(temp_path)
         
        # how we invoke yum-utils depends on whether this is RHN content or not.

       
        # this is the somewhat more-complex RHN case.
        # NOTE: this requires that you have entitlements for the server and you give the mirror as rhn://$channelname
        if not self.mirror_locally:
            utils.die("rhn:// repos do not work with --mirror-locally=1")

        if has_rpm_list:
            logger.warning("warning: --rpm-list is not supported for RHN content")
        rest = self.mirror[6:] # everything after rhn://
        rflags = self.settings.reposync_flags
        cmd = "/usr/bin/reposync %s -r %s --download_path=%s" % (rflags, rest, self.settings.webdir+"/repo_mirror")
        if self.name != rest:
            args = { "name" : self.name, "rest" : rest }
            utils.die(logger,"ERROR: repository %(name)s needs to be renamed %(rest)s as the name of the cobbler repository must match the name of the RHN channel" % args)

        if self.arch == "i386":
            # counter-intuitive, but we want the newish kernels too
            self.arch = "i686"

        if self.arch != "":
            cmd = "%s -a %s" % (cmd, self.arch)

        # now regardless of whether we're doing yumdownloader or reposync
        # or whether the repo was http://, ftp://, or rhn://, execute all queued
        # commands here.  Any failure at any point stops the operation.

        if self.mirror_locally:
            rc = utils.subprocess_call(logger, cmd)
            # Don't die if reposync fails, it is logged
            # if rc !=0:
            #     utils.die(logger,"cobbler reposync failed")

        # some more special case handling for RHN.
        # create the config file now, because the directory didn't exist earlier

        temp_file = self.create_local_file(temp_path, False)

        # now run createrepo to rebuild the index

        if self.mirror_locally:
            os.path.walk(dest_path, self.createrepo_walker, logger)

        # create the config file the hosts will use to access the repository.

        self.create_local_file(dest_path)

class AptRepo ( Repo ) :

    breed = "apt"

    def sync(self, logger):

        # warn about not having mirror program.

        mirror_program = "/usr/bin/debmirror"
        if not os.path.exists(mirror_program):
            utils.die(logger,"no %s found, please install it"%(mirror_program))

        cmd = ""                  # command to run
        has_rpm_list = False      # flag indicating not to pull the whole repo

        # detect cases that require special handling

        if self.rpm_list != "" and self.rpm_list != []:
            utils.die(logger,"has_rpm_list not yet supported on apt repos")

        if not self.arch:
            utils.die(logger,"Architecture is required for apt repositories")

        # built destination path for the repo
        dest_path = os.path.join("/var/www/cobbler/repo_mirror", self.name)
         
        if self.mirror_locally:
            mirror = repo.mirror.replace("@@suite@@",repo.os_version)

            idx = mirror.find("://")
            method = mirror[:idx]
            mirror = mirror[idx+3:]

            idx = mirror.find("/")
            host = mirror[:idx]
            mirror = mirror[idx+1:]

            idx = mirror.rfind("/dists/")
            suite = mirror[idx+7:]
            mirror = mirror[:idx]

            mirror_data = "--method=%s --host=%s --root=%s --dist=%s " % ( method , host , mirror , suite )

            # FIXME : flags should come from repo instead of being hardcoded

            rflags = "--passive --nocleanup"
            for x in self.yumopts:
                if self.yumopts[x]:
                    rflags += " %s %s" % ( x , self.yumopts[x] ) 
                else:
                    rflags += " %s" % x 
            cmd = "%s %s %s %s" % (mirror_program, rflags, mirror_data, dest_path)
            if self.arch == "src":
                cmd = "%s --source" % cmd
            else:
                arch = self.arch
                if arch == "x86":
                   arch = "i386" # FIX potential arch errors
                if arch == "x86_64":
                   arch = "amd64" # FIX potential arch errors
                cmd = "%s --nosource -a %s" % (cmd, arch)
                    
            rc = utils.subprocess_call(logger, cmd)
            if rc !=0:
                utils.die(logger,"cobbler reposync failed")


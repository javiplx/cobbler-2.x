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

VALID_REPO_BREEDS = ( "rsync", "rhn", "yum", "apt" )

# this datastructure is described in great detail in item_distro.py -- read the comments there.

FIELDS = [
  ["arch","",0,"Arch",True,"ex: i386, x86_64",['i386','x86_64','ia64','ppc','s390', 'noarch', 'src'],"str"],
  ["breed","",0,"Breed",True,"",VALID_REPO_BREEDS,"str"],
  ["comment","",0,"Comment",True,"Free form text description",0,"str"],
  ["ctime",0,0,"",False,"",0,"float"],
  ["depth",2,0,"",False,"",0,"float"],
  ["keep_updated",True,0,"Keep Updated",True,"Update this repo on next 'cobbler reposync'?",0,"bool"],
  ["mirror_locally",True,0,"Mirror locally",True,"Copy files or just reference the repo externally?",0,"bool"],
  ["mirror",None,0,"Mirror",True,"Address of yum or rsync repo to mirror",0,"str"],
  ["mtime",0,0,"",False,"",0,"float"],
  ["name","",0,"Name",True,"Ex: f10-i386-updates",0,"str"],
  ["owners","SETTINGS:default_ownership",0,"Owners",True,"Owners list for authz_ownership (space delimited)",[],"list"],
  ["parent",None,0,"",False,"",0,"str"],
  ["repo_version","",0,"Repo/OS Version",True,"ex: rhel4",0,"str"],
  ["uid",None,0,"",False,"",0,"str"],
]

class Repo(item.Item):

    TYPE_NAME = _("repo")
    COLLECTION_TYPE = "repo"
    breed = None

    def Factory(config,seed_data):
        if seed_data.get( 'breed' ) == "rsync" :
            obj = RsyncRepo(config)
        if seed_data.get( 'breed' ) == "yum" :
            obj = YumRepo(config)
        elif seed_data.get( 'breed' ) == "rhn" :
            obj = RhnRepo(config)
        elif seed_data.get( 'breed' ) == "apt" :
            obj = AptRepo(config)
        else:
            obj = VoidRepo(config)
        return obj.from_datastruct(seed_data)
    Factory = staticmethod(Factory)

    def sync(self, logger):
        utils.die(logger,"unable to sync repo (%s), unknown or unsupported repo type (%s)" % (self.name, self.breed))

    def make_clone(self):
        ds = self.to_datastruct()
        cloned = self.Factory(self.config,ds)
        if isinstance(cloned, VoidRepo):
            raise CX(_("Wrong Repo cloning. Product is a VoidRepo object"))
        return cloned

    def get_fields(self):
        return FIELDS

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

    def set_breed(self,breed):
        if breed != self.breed:
            raise CX(_("Setting breed on %s to an invalid value (%s)") % (self,breed))

    def set_repo_version(self,repo_version):
        if repo_version:
            if not self.breed :
               raise CX(_("cannot set --repo-version without setting --breed first"))
            self.repo_version = repo_version

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

class VoidRepo ( Repo ) :

    def guess_breed(self,mirror):
        if mirror.startswith("http://") or mirror.startswith("ftp://"):
            return "yum"
        elif mirror.startswith("rhn://"):
            return "rhn"
        else:
            return "rsync"

class _RpmRepo ( Repo ) :

    def create_local_file(self, dest_path, logger, output=True):
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
        logger.debug("creating: %s" % fname)
        if not os.path.exists(dest_path):
            utils.mkdir(dest_path)
        config_file = open(fname, "w+")
        config_file.write("[%s]\n" % self.name)
        config_file.write("name=%s\n" % self.name)
        optenabled = False
        optgpgcheck = False
        if output:
            line = "baseurl=http://${server}/cobbler/repo_mirror/%s\n" % self.name
  
            config_file.write(line)
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
        # FIXME: potentially might want a way to turn this on/off on a per-repo basis
        if not optgpgcheck:
            config_file.write("gpgcheck=0\n")
        config_file.close()
        return fname 

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

class RsyncRepo ( _RpmRepo ) :

    breed = "rsync"

    def sync(self, logger):

        if self.rpm_list :
            logger.warning("--rpm-list is not supported for rsync'd repositories")

        # FIXME: don't hardcode
        dest_path = os.path.join(self.settings.webdir+"/repo_mirror", self.name)

        # FIXME : check any interference due introduced by utils function, which preserve permission & owner
        if not utils.rsync_files( self.mirror , dest_path ,  "--verbose --delete" , logger ) :
            utils.die(logger,"cobbler reposync failed")

        os.path.walk(dest_path, self.createrepo_walker, logger)
        self.create_local_file(dest_path, logger)

class YumRepo ( _RpmRepo ) :

    breed = "yum"

    def sync(self, logger):

        if not os.path.exists("/usr/bin/reposync"):
            utils.die(logger,"no /usr/bin/reposync found, please install yum-utils")

        # create yum config file for use by reposync
        dest_path = os.path.join(self.settings.webdir+"/repo_mirror", self.name)
        temp_path = os.path.join(dest_path, ".origin")

        if not os.path.isdir(temp_path):
            # FIXME: there's a chance this might break the RHN D/L case
            os.makedirs(temp_path)
         
        # create the config file that yum will use for the copying

        temp_file = self.create_local_file(temp_path, logger, False)

        if not self.rpm_list:
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

        else:

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
        os.path.walk(dest_path, self.createrepo_walker, logger)

        # create the config file the hosts will use to access the repository.
        self.create_local_file(dest_path, logger)

class RhnRepo ( _RpmRepo ) :

    breed = "rhn"

    def sync(self, logger):

        if self.rpm_list:
            logger.warning("--rpm-list is not supported for RHN content")

        if not os.path.exists("/usr/bin/reposync"):
            utils.die(logger,"no /usr/bin/reposync found, please install yum-utils")

        # create yum config file for use by reposync
        # FIXME: don't hardcode
        dest_path = os.path.join(self.settings.webdir+"/repo_mirror", self.name)
        temp_path = os.path.join(dest_path, ".origin")

        if not os.path.isdir(temp_path):
            # FIXME: there's a chance this might break the RHN D/L case
            os.makedirs(temp_path)
         
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

        # Don't die if reposync fails, it is logged
        utils.subprocess_call(logger, cmd)

        # some more special case handling for RHN.
        # create the config file now, because the directory didn't exist earlier

        temp_file = self.create_local_file(temp_path, logger, False)

        # now run createrepo to rebuild the index

        os.path.walk(dest_path, self.createrepo_walker, logger)

        # create the config file the hosts will use to access the repository.

        self.create_local_file(dest_path, logger)


import repolib

class RepoConf ( repolib.config.MirrorConf ) :

    def __init__ ( self , repo ) :
        repolib.config.MirrorConf.__init__( self , repo.name , repo , None )
        self.update( { 'detached':True ,
                 'class':"standard" , 'subdir':False , 'filters':{} ,
                 'params':{ 'usegpg':False , 'pkgvflags':"SKIP_NONE" } } )
        # NOTE : using dict(repolib.config.default_params) for params forces gpg verification

    def read ( self , repo ) :
        self['type'] = repo.breed
        self['version'] = repo.repo_version
        self['destdir'] = os.path.join(repo.settings.webdir,"repo_mirror",repo.name)
        self['url'] = repo.mirror
        if repo.arch :
            self['architectures'] = (repo.arch,)


class AptRepo ( Repo , RepoConf ) :

    def __init__(self,config,is_subobject=False):
        Repo.__init__(self,config,is_subobject)
        self.breed = "apt"
        RepoConf.__init__(self,self)

    def from_datastruct(self,seed_data):
        obj = Repo.from_datastruct(self,seed_data)
        obj.read( obj )
        return obj

    def set_mirror(self,mirror):
        Repo.set_mirror( self , mirror )
        self['url'] = mirror
        return True

    def set_repo_version(self,repo_version):
        Repo.set_repo_version( self , repo_version )
        self['version'] = repo_version

    def set_arch(self,arch):
        self['architectures'] = ( arch ,)
        return Repo.set_arch( self , arch )

    def sync(self, logger):
        repolib.logger = logger
        repo = repolib.debian_repository( self )

        meta_files = repo.get_metafile()
        if not meta_files or meta_files.values().count( False ) :
            logger.error("cobbler reposync failed, some files not downloaded")
            return

        repo.build_local_tree()
        local_repodata = repo.write_master_file(meta_files)

        download_pkgs = repo.get_download_list()
        download_pkgs.start()
        missing_pkgs = {}
        for subrepo in repo.subrepos.values() :
            packages = subrepo.get_metafile( local_repodata )
            if isinstance(packages,bool) or repo.mode == "metadata" :
                continue
            download , missing = subrepo.get_package_list( packages , {} , repo.filters )
            download_pkgs.extend( download )
            missing_pkgs.update( dict.fromkeys( missing ) )

        download_pkgs.finish()
        download_pkgs.join()


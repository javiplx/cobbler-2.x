"""
This is some of the code behind 'cobbler sync'.

Copyright 2006-2009, Red Hat, Inc
Michael DeHaan <mdehaan@redhat.com>
John Eckersberg <jeckersb@redhat.com>

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

import os
import os.path
import shutil
import time
import sys
import glob
import re
import traceback
import errno
from utils import popen2
from shlex import shlex


import utils
from cexceptions import *
import templar

import item_distro
import item_profile
import item_repo
import item_system

from utils import _

def register():
   """
   The mandatory cobbler module registration hook.
   """
   return "manage/import"


from cobbler.manage_import_base import ImportManagerBase

class ImportFreeBSDManager ( ImportManagerBase ) :

    __breed__ = "freebsd"

    signatures = [
          'etc/freebsd-update.conf',
          'boot/frames.4th',
          '8.2-RELEASE',
       ]

    def get_valid_arches(self):
        return ["i386", "ia64", "ppc", "ppc64", "s390", "s390x", "x86_64", "x86",]

    def get_valid_breeds(self):
        return ["freebsd",]

    def get_valid_os_versions(self):
        return ["8.2",]

    def get_valid_repo_breeds(self):
        return ["rsync", "rhn", "yum",]

    def get_release_files(self):
        data = glob.glob(os.path.join(self.get_rootdir(), "*RELEASE"))
        data2 = []
        for x in data:
            b = os.path.basename(x)
            for valid_os in self.get_valid_os_versions():
                if b.find(valid_os) != -1:
                    data2.append(x)
        return data2

    def is_initrd(self,filename):
        if filename == "mfsroot.gz":
            return True
        return False

    def is_kernel(self,filename):
        if filename in ( "pxeboot" , "pxeboot.bs" ):
            return True
        return False

    def get_name_from_dirname(self,dirname):
        return self.mirror_name + "-".join(utils.path_tail(os.path.dirname(self.path),dirname).split("/"))

    def get_local_tree(self, distro):
        base = self.get_rootdir()
        dest_link = os.path.join(self.settings.webdir, "links", distro.name)
        # create the links directory only if we are mirroring because with
        # SELinux Apache can't symlink to NFS (without some doing)
        if not os.path.exists(dest_link):
            try:
                os.symlink(base, dest_link)
            except:
                # this shouldn't happen but I've seen it ... debug ...
                self.logger.warning("symlink creation failed: %s, %s" % (base,dest_link))
        # how we set the tree depends on whether an explicit network_root was specified
        return "http://@@http_server@@/cblr/links/%s" % (distro.name)

    def get_rootdir(self):
        return self.rootdir

    def repo_scanner(self,distro,dirname,fnames):

        matches = {}
        for x in fnames:
            if x == "base" or x == "repodata":
                self.logger.info("processing repo at : %s" % dirname)
                # only run the repo scanner on directories that contain a comps.xml
                gloob1 = glob.glob("%s/%s/*comps*.xml" % (dirname,x))
                if len(gloob1) >= 1:
                    if matches.has_key(dirname):
                        self.logger.info("looks like we've already scanned here: %s" % dirname)
                        continue
                    self.logger.info("need to process repo/comps: %s" % dirname)
                    self.process_comps_file(dirname, distro)
                    matches[dirname] = 1
                else:
                    self.logger.info("directory %s is missing xml comps file, skipping" % dirname)
                    continue

    def process_comps_file(self, comps_path, distro):
        """
        When importing Fedora/EL certain parts of the install tree can also be used
        as yum repos containing packages that might not yet be available via updates
        in yum.  This code identifies those areas.
        """

        processed_repos = {}

        masterdir = "repodata"
        if not os.path.exists(os.path.join(comps_path, "repodata")):
            # older distros...
            masterdir = "base"

        # figure out what our comps file is ...
        self.logger.info("looking for %(p1)s/%(p2)s/*comps*.xml" % { "p1" : comps_path, "p2" : masterdir })
        files = glob.glob("%s/%s/*comps*.xml" % (comps_path, masterdir))
        if len(files) == 0:
            self.logger.info("no comps found here: %s" % os.path.join(comps_path, masterdir))
            return # no comps xml file found

        # pull the filename from the longer part
        comps_file = files[0].split("/")[-1]

        try:
            # store the yum configs on the filesystem so we can use them later.
            # and configure them in the answerfile post, etc

            counter = len(distro.source_repos)

            # find path segment for yum_url (changing filesystem path to http:// trailing fragment)
            seg = comps_path.rfind("ks_mirror")
            urlseg = comps_path[seg+10:]

            # write a yum config file that shows how to use the repo.
            if counter == 0:
                dotrepo = "%s.repo" % distro.name
            else:
                dotrepo = "%s-%s.repo" % (distro.name, counter)

            fname = os.path.join(self.settings.webdir, "ks_mirror", "config", "%s-%s.repo" % (distro.name, counter))

            repo_url = "http://@@http_server@@/cobbler/ks_mirror/config/%s-%s.repo" % (distro.name, counter)
            repo_url2 = "http://@@http_server@@/cobbler/ks_mirror/%s" % (urlseg)

            distro.source_repos.append([repo_url,repo_url2])

            # NOTE: the following file is now a Cheetah template, so it can be remapped
            # during sync, that's why we have the @@http_server@@ left as templating magic.
            # repo_url2 is actually no longer used. (?)

            config_file = open(fname, "w+")
            config_file.write("[core-%s]\n" % counter)
            config_file.write("name=core-%s\n" % counter)
            config_file.write("baseurl=http://@@http_server@@/cobbler/ks_mirror/%s\n" % (urlseg))
            config_file.write("enabled=1\n")
            config_file.write("gpgcheck=0\n")
            config_file.write("priority=$yum_distro_priority\n")
            config_file.close()

            # don't run creatrepo twice -- this can happen easily for Xen and PXE, when
            # they'll share same repo files.

            if not processed_repos.has_key(comps_path):
                utils.remove_yum_olddata(comps_path)
                #cmd = "createrepo --basedir / --groupfile %s %s" % (os.path.join(comps_path, masterdir, comps_file), comps_path)
                cmd = "createrepo %s --groupfile %s %s" % (self.settings.createrepo_flags,os.path.join(comps_path, masterdir, comps_file), comps_path)
                utils.subprocess_call(self.logger, cmd, shell=True)
                processed_repos[comps_path] = 1
                # for older distros, if we have a "base" dir parallel with "repodata", we need to copy comps.xml up one...
                p1 = os.path.join(comps_path, "repodata", "comps.xml")
                p2 = os.path.join(comps_path, "base", "comps.xml")
                if os.path.exists(p1) and os.path.exists(p2):
                    shutil.copyfile(p1,p2)

        except:
            self.logger.error("error launching createrepo (not installed?), ignoring")
            utils.log_exc(self.logger)

    def add_entry(self,dirname,kernel,initrd):

        proposed_name = self.get_proposed_name(dirname,kernel)
        proposed_arch = self.get_proposed_arch(dirname)

        if self.arch and proposed_arch and self.arch != proposed_arch:
            utils.die(self.logger,"Arch from pathname (%s) does not match with supplied one %s"%(proposed_arch,self.arch))

        archs = self.learn_arch_from_tree()
        if not archs:
            if self.arch:
                archs.append( self.arch )
        else:
            if self.arch and self.arch not in archs:
                # FIXME : the base class add_entry method issues message with self.get_pkgdir()
                utils.die(self.logger, "Given arch (%s) not found on imported tree %s"%(self.arch,self.get_rootdir()))
        if proposed_arch:
            if archs and proposed_arch not in archs:
                # FIXME : the base class add_entry method issues message with self.get_pkgdir()
                self.logger.warning("arch from pathname (%s) not found on imported tree %s" % (proposed_arch,self.get_rootdir()))
                return

            archs = [ proposed_arch ]

        if len(archs)>1:
            if self.breed in ( "redhat" , "suse" ):
                self.logger.warning("directory %s holds multiple arches : %s" % (dirname, archs))
                return
            self.logger.warning("- Warning : Multiple archs found : %s" % (archs))

        distros_added = []

        for pxe_arch in archs:
            name = proposed_name + "-" + pxe_arch
            existing_distro = self.distros.find(name=name)

            if existing_distro is not None:
                self.logger.warning("skipping import, as distro name already exists: %s" % name)
                continue

            else:
                self.logger.info("creating new distro: %s" % name)
                distro = self.config.new_distro()

            # FIXME : the base class skips here if name includes -autoboot

            distro.set_name(name)
            distro.set_kernel(kernel)
            distro.set_initrd(initrd)
            distro.set_arch(pxe_arch)
            distro.set_breed(self.breed)
            # If a version was supplied on command line, we set it now
            if self.os_version:
                distro.set_os_version(self.os_version)

            self.distros.add(distro,save=True)
            distros_added.append(distro)

            existing_profile = self.profiles.find(name=name)

            # see if the profile name is already used, if so, skip it and
            # do not modify the existing profile

            if existing_profile is None:
                self.logger.info("creating new profile: %s" % name)
                #FIXME: The created profile holds a default answerfile, and should be breed specific
                profile = self.config.new_profile()
            else:
                self.logger.info("skipping existing profile, name already exists: %s" % name)
                continue

            # save our minimal profile which just points to the distribution and a good
            # default answer file

            profile.set_name(name)
            profile.set_distro(name)
            profile.set_kickstart(self.kickstart_file)

            if name.find("vmware") != -1 or self.breed in ( "vmware" , "freebsd" ):
                profile.set_virt_type("vmware")
            elif name.find("-xen") != -1:
                profile.set_virt_type("xenpv")
            else:
                profile.set_virt_type("qemu")

            # save our new profile to the collection

            self.profiles.add(profile,save=True)

        return distros_added

    def get_proposed_name(self,dirname,kernel=None):

        if self.network_root is not None:
            name = self.get_name_from_dirname(dirname)
        else:
            # remove the part that says /var/www/cobbler/ks_mirror/name
            name = "-".join(dirname.split("/")[5:])
        
        # Clean up name
        name = name.replace("-boot","")

        for separator in [ '-' , '_'  , '.' ] :
            for arch in [ "i386" , "x86_64" , "ia64" , "ppc64", "ppc32", "ppc", "x86" , "s390x", "s390" , "386" , "amd" ]:
                name = name.replace("%s%s" % ( separator , arch ),"")

        return name

    def match_kernelarch_file(self, filename):

        if not filename.endswith("rpm") and not filename.endswith("deb"):
            return False
        for match in ["kernel-header", "kernel-source", "kernel-smp", "kernel-largesmp", "kernel-hugemem", "linux-headers-", "kernel-devel", "kernel-"]:
            if filename.find(match) != -1:
                return True
        return False

    def kickstart_finder(self,distros_added):
        for profile in self.profiles:
            distro = self.distros.find(name=profile.get_conceptual_parent().name)
            if distro is None or not (distro in distros_added):
                continue

            kdir = os.path.dirname(distro.kernel)
            if self.kickstart_file == None:
                for rpm in self.get_release_files():
                    # FIXME : This redhat specific check should go into the importer.find_release_files method
                    if rpm.find("notes") != -1:
                        continue
                    results = self.scan_pkg_filename(rpm)
                    # FIXME : If os is not found on tree but set with CLI, no answerfile is searched
                    if results is None:
                        self.logger.warning("No version found on imported tree")
                        continue
                    (flavor, major, minor) = results
                    version , ks = self.set_variance(flavor, major, minor, distro.arch)
                    if self.os_version:
                        if self.os_version != version:
                            utils.die(self.logger,"CLI version differs from tree : %s vs. %s" % (self.os_version,version))
                    ds = self.get_datestamp()
                    distro.set_comment(version)
                    distro.set_os_version(version)
                    if ds is not None:
                        distro.set_tree_build_time(ds)
                    profile.set_kickstart(ks)
                    if flavor == "freebsd":
                        self.logger.info("This is FreeBSD - adding boot files to fetchable files")
                        # add fetchable files to distro
                        distro.set_fetchable_files('boot/mfsroot.gz=$initrd boot/*=$webdir/ks_mirror/$distro/boot/')
                    self.profiles.add(profile,save=True)

            self.configure_tree_location(distro)
            self.distros.add(distro,save=True) # re-save
            self.api.serialize()

    def learn_arch_from_tree(self):
        result = {}
        # FIXME : this is called only once, should not be a walk
        # FIXME : base class uses get_pkgdir() to start walking
        if self.get_rootdir():
            os.path.walk(self.get_rootdir(), self.arch_walker, result)
        if result.pop("amd64",False):
            result["x86_64"] = 1
        if result.pop("i686",False):
            result["i386"] = 1
        return result.keys()

    def scan_pkg_filename(self, file):
        release_file = os.path.basename(file)

        if release_file.lower().find("release") != -1:
            flavor = "freebsd"
            match = re.search(r'(\d).(\d)-RELEASE', release_file)
            if match:
                major   = match.group(1)
                minor   = match.group(2)
            else:
                # FIXME: what should we do if the re fails above?
                return None

        return (flavor, major, minor)

    def get_datestamp(self):
        pass

    def set_variance(self, flavor, major, minor, arch):

        os_version = "%s%s.%s" % (flavor,major,minor)
        answerfile = "/var/lib/cobbler/kickstarts/default.ks"

        return os_version, answerfile

# ==========================================================================

def get_import_manager(config,logger):
    return ImportFreeBSDManager(config,logger)

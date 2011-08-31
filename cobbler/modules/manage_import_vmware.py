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
import traceback
import errno
import re
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

class ImportVMWareManager ( ImportManagerBase ) :

    __breed__ = "vmware"

    signatures = [
           'VMware/RPMS',
           'imagedd.bz2',
       ]

    def get_valid_arches(self):
        return ["i386", "x86_64", "x86",]

    def get_valid_breeds(self):
        return ["vmware",]

    def get_valid_os_versions(self):
        return ["esx4","esxi"]

    def get_valid_repo_breeds(self):
        return ["rsync", "rhn", "yum",]

    def get_release_files(self):
        data = glob.glob(os.path.join(self.get_pkgdir(), "vmware-esx-vmware-release-*"))
        data2 = []
        for x in data:
            b = os.path.basename(x)
            if b.find("vmware") != -1:
                data2.append(x)
        if len(data2) == 0:
            # ESXi maybe?
            return glob.glob(os.path.join(self.get_rootdir(), "vmkernel.gz"))
        return data2

    def is_initrd(self,filename):
        if ( filename.startswith("initrd") or filename.startswith("ramdisk.image.gz") or filename.startswith("vmkboot.gz") ) and filename != "initrd.size":
            return True
        return False

    def is_kernel(self,filename):
        if ( filename.startswith("vmlinu") or filename.startswith("kernel.img") or filename.startswith("linux") or filename.startswith("mboot.c32") ) and filename.find("initrd") == -1:
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

    def repo_finder(self, distros_added):
        pass

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
            # and configure them in the kickstart post, etc

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
                    # FIXME : If os is not found on tree but set with CLI, no kickstart is searched
                    if results is None:
                        self.logger.warning("No version found on imported tree")
                        continue
                    (flavor, major, minor, release, update) = results
                    version , ks = self.set_variance(flavor, major, minor, (release, update))
                    if self.os_version:
                        if self.os_version != version:
                            utils.die(self.logger,"CLI version differs from tree : %s vs. %s" % (self.os_version,version))
                    ds = self.get_datestamp()
                    distro.set_comment("%s.%s.%s update %s" % (version,minor,release,update))
                    distro.set_os_version(version)
                    if ds is not None:
                        distro.set_tree_build_time(ds)
                    profile.set_kickstart(ks)
                    if flavor == "esxi":
                        self.logger.info("This is an ESXi distro - adding extra PXE files to boot-files list")
                        # add extra files to boot_files in the distro
                        boot_files = ''
                        for file in ('vmkernel.gz','sys.vgz','cim.vgz','ienviron.vgz','install.vgz'):
                           boot_files += '$img_path/%s=%s/%s ' % (file,self.path,file)
                        distro.set_boot_files(boot_files.strip())
                    self.profiles.add(profile,save=True)

            self.configure_tree_location(distro)
            self.distros.add(distro,save=True) # re-save
            self.api.serialize()

    def scan_pkg_filename(self, file):
        rpm_file = os.path.basename(file)

        if rpm_file.lower().find("-esx-") != -1:
            flavor = "esx"
            match = re.search(r'release-(\d)+-(\d)+\.(\d)+\.(\d)+-(\d)\.', rpm_file)
            if match:
                major   = match.group(2)
                minor   = match.group(3)
                release = match.group(4)
                update  = match.group(5)
            else:
                # FIXME: what should we do if the re fails above?
                return None
        elif rpm_file.lower() == "vmkernel.gz":
            flavor  = "esxi"
            major   = 0
            minor   = 0
            release = 0
            update  = 0

            # this should return something like:
            # VMware ESXi 4.1.0 [Releasebuild-260247], built on May 18 2010
            # though there will most likely be multiple results
            scan_cmd = 'gunzip -c %s | strings | grep -i "^vmware esxi"' % file
            (data,rc) = utils.subprocess_sp(self.logger, scan_cmd)
            lines = data.split('\n')
            m = re.compile(r'ESXi (\d)+\.(\d)+\.(\d)+ \[Releasebuild-([\d]+)\]')
            for line in lines:
                match = m.search(line)
                if match:
                    major   = match.group(1)
                    minor   = match.group(2)
                    release = match.group(3)
                    update  = match.group(4)
                    break
            else:
                return None

        #self.logger.info("DEBUG: in scan_pkg_filename() - major=%s, minor=%s, release=%s, update=%s" % (major,minor,release,update))
        return (flavor, major, minor, release, update)

    def get_datestamp(self):
        pass

    def set_variance(self, flavor, major, minor, arch):
        release , update = arch
        os_version = "%s%s" % (flavor, major)
        if flavor == "esx4":
            ks = "/var/lib/cobbler/kickstarts/esx.ks"
        elif flavor == "esxi4":
            ks = "/var/lib/cobbler/kickstarts/esxi.ks"
        else:
            ks = "/var/lib/cobbler/kickstarts/default.ks"
        return os_version , ks

# ==========================================================================

def get_import_manager(config,logger):
    return ImportVMWareManager(config,logger)

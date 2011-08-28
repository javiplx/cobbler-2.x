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


from cobbler.manage_import_base import ImportBaseManager

class ImportVMWareManager ( ImportBaseManager ) :

    __breed_str__ = "vmware"

    signatures = [
           'VMware/RPMS',
           'imagedd.bz2',
       ]

    # required function for import modules
    def get_valid_arches(self):
        return ["i386", "x86_64", "x86",]

    # required function for import modules
    def get_valid_breeds(self):
        return ["vmware",]

    # required function for import modules
    def get_valid_os_versions(self):
        return ["esx4","esxi"]

    def get_valid_repo_breeds(self):
        return ["rsync", "rhn", "yum",]

    def get_release_files(self):
        """
        Find distro release packages.
        """
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

    def get_install_tree(self, distro, base):
            # NOTE : this is the same than in RedHat importer
            dest_link = os.path.join(self.settings.webdir, "links", distro.name)
            # create the links directory only if we are mirroring because with
            # SELinux Apache can't symlink to NFS (without some doing)
            if not os.path.exists(dest_link):
                try:
                    os.symlink(base, dest_link)
                except:
                    # this shouldn't happen but I've seen it ... debug ...
                    self.logger.warning("symlink creation failed: %(base)s, %(dest)s") % { "base" : base, "dest" : dest_link }
            # how we set the tree depends on whether an explicit network_root was specified
            return "http://@@http_server@@/cblr/links/%s" % (distro.name)

    def repo_finder(self, distros_added):
        self.logger.warning( "repo_finder magic not possible (yet) without mirroring" )

    def valid_initrd(self,name):
        if name.startswith("initrd") or name.startswith("ramdisk.image.gz") or name.startswith("vmkboot.gz") :
            return True
        return False

    def valid_kernel(self,name):
        if name.startswith("vmlinu") or name.startswith("kernel.img") or name.startswith("linux") or name.startswith("mboot.c32") :
            return True
        return False

    def get_name_from_path(self,dirname):
        return self.mirror_name + "-".join(utils.path_tail(os.path.dirname(self.path),dirname).split("/"))

    def kickstart_finder(self,distros_added):
        """
        For all of the profiles in the config w/o a kickstart, use the
        given kickstart file, or look at the kernel path, from that,
        see if we can guess the distro, and if we can, assign a kickstart
        if one is available for it.
        """
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
                    version , ks = self.set_variance(flavor, major, minor, release, update, distro.arch)
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

    def get_rootdir(self):
        return self.rootdir

    def match_kernelarch_file(self, filename):
        """
        Is the given filename a kernel filename?
        """

        if not filename.endswith("rpm") and not filename.endswith("deb"):
            return False
        for match in ["kernel-header", "kernel-source", "kernel-smp", "kernel-largesmp", "kernel-hugemem", "linux-headers-", "kernel-devel", "kernel-"]:
            if filename.find(match) != -1:
                return True
        return False

    def scan_pkg_filename(self, rpm):
        """
        Determine what the distro is based on the release package filename.
        """
        rpm_file = os.path.basename(rpm)

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
            scan_cmd = 'gunzip -c %s | strings | grep -i "^vmware esxi"' % rpm
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
        """
        Based on a VMWare tree find the creation timestamp
        """
        pass

    def set_variance(self, flavor, major, minor, release, update, arch):
        """
        Set distro specific versioning.
        """
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

"""
--
-- Copyright (c) 2011 Novell
-- Uwe Gansert <ug@suse.de>
--
-- This software is licensed to you under the GNU General Public License,
-- version 2 (GPLv2). There is NO WARRANTY for this software, express or
-- implied, including the implied warranties of MERCHANTABILITY or FITNESS
-- FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv2
-- along with this software; if not, see
-- http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.
--
--

"""

import os
import os.path
import shutil
import time
import sys
import glob
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

class ImportSuseManager ( ImportManagerBase ) :

    __breed__ = "suse"

    signatures = [
          'suse'
       ]

    def get_valid_arches(self):
        return ["i386", "ia64", "ppc", "ppc64", "s390", "s390x", "x86_64", "x86",]

    def get_valid_breeds(self):
        return ["suse",]

    def get_valid_os_versions(self):
        return []

    def get_valid_repo_breeds(self):
        return ["yast", "rsync", "yum"]

    def get_release_files(self):
        data = glob.glob(os.path.join(self.get_pkgdir(), "*release-*"))
        data2 = []
        for x in data:
            b = os.path.basename(x)
# FIXME
#            if b.find("fedora") != -1 or \
#               b.find("redhat") != -1 or \
#               b.find("centos") != -1:
#                data2.append(x)
        return data2

    def is_initrd(self,filename):
        if ( filename.startswith("initrd") or filename.startswith("ramdisk.image.gz") ) and filename != "initrd.size":
            return True
        return False

    def is_kernel(self,filename):
        if ( filename.startswith("vmlinu") or filename.startswith("kernel.img") or filename.startswith("linux") ) and filename.find("initrd") == -1:
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
                os.symlink(base + "-" + distro.arch, dest_link)
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
                    matches[dirname] = 1
                    self.process_comps_file(dirname, distro)
                else:
                    self.logger.info("directory %s is missing xml comps file, skipping" % dirname)
                    continue

    def process_comps_file(self, comps_path, distro):
        pass

    def distro_adder(self,distros_added,dirname,fnames):

        # FIXME: If there are more than one kernel or initrd image on the same directory,
        # results are unpredictable

        initrd = None
        kernel = None

        # make sure we don't mismatch PAE and non-PAE types
        pae_initrd = None
        pae_kernel = None

        for x in fnames:
            adtls = []

            fullname = os.path.join(dirname,x)
            if os.path.islink(fullname) and os.path.isdir(fullname):
                if fullname.startswith(self.path):
                    # Prevent infinite loop with Sci Linux 5
                    self.logger.warning("avoiding symlink loop")
                    continue
                self.logger.info("following symlink: %s" % fullname)
                os.path.walk(fullname, self.distro_adder, distros_added)

            if self.is_initrd(x):
                if x.find("PAE") == -1:
                    initrd = os.path.join(dirname,x)
                else:
                    pae_initrd = os.path.join(dirname, x)

            if self.is_kernel(x):
                if x.find("PAE") == -1:
                    kernel = os.path.join(dirname,x)
                else:
                    pae_kernel = os.path.join(dirname, x)

            # if we've collected a matching kernel and initrd pair, turn the in and add them to the list
            if initrd is not None and kernel is not None and dirname.find("isolinux") == -1:
                adtls.append(self.add_entry(dirname,kernel,initrd))
                kernel = None
                initrd = None
            elif pae_initrd is not None and pae_kernel is not None and dirname.find("isolinux") == -1:
                adtls.append(self.add_entry(dirname,pae_kernel,pae_initrd))
                pae_kernel = None
                pae_initrd = None

            for adtl in adtls:
                distros_added.extend(adtl)

    def get_proposed_name(self,dirname,kernel=None):

        if self.network_root is not None:
            name = self.get_name_from_dirname(dirname)
        else:
            # remove the part that says /var/www/cobbler/ks_mirror/name
            name = "-".join(dirname.split("/")[5:])

        if kernel is not None and kernel.find("PAE") != -1:
            name = name + "-PAE"
        if kernel is not None and kernel.find("xen") != -1:
            name = name + "-xen"

        # we have our kernel in ../boot/<arch>/vmlinuz-xen and
        # .../boot/<arch>/loader/vmlinuz
        #
        name = name.replace("-loader","")
        name = name.replace("-boot","")

        # some paths above the media root may have extra path segments we want
        # to clean up
        name = name.replace("-os","")
        name = name.replace("-tree","")
        name = name.replace("srv-www-cobbler-", "")
        name = name.replace("var-www-cobbler-", "")
        name = name.replace("ks_mirror-","")
        name = name.replace("--","-")

        for separator in [ '-' , '_'  , '.' ] :
            for arch in [ "i386" , "x86_64" , "ia64" , "ppc64", "ppc32", "ppc", "x86" , "s390x", "s390" , "386" , "amd" ]:
                name = name.replace("%s%s" % ( separator , arch ),"")

        return name

    def match_kernelarch_file(self, filename):

        if not filename.endswith("rpm") and not filename.endswith("deb"):
            return False
        for match in ["kernel-header", "kernel-source", "kernel-smp", "kernel-default", "kernel-desktop", "linux-headers-", "kernel-devel", "kernel-"]:
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
                    results = self.scan_pkg_filename(rpm)
                    # FIXME : If os is not found on tree but set with CLI, no kickstart is searched
                    if results is None:
                        self.logger.warning("No version found on imported tree")
                        continue
                    (flavor, major, minor) = results
                    version , ks = self.set_variance(flavor, major, minor, distro.arch)
                    if self.os_version:
                        if self.os_version != version:
                            utils.die(self.logger,"CLI version differs from tree : %s vs. %s" % (self.os_version,version))
                    ds = self.get_datestamp()
                    distro.set_comment("%s.%s" % (version, int(minor)))
                    distro.set_os_version(version)
                    if ds is not None:
                        distro.set_tree_build_time(ds)
                    profile.set_kickstart(ks)
                    self.profiles.add(profile,save=True)

            self.configure_tree_location(distro)
            self.distros.add(distro,save=True) # re-save
            self.api.serialize()

    def scan_pkg_filename(self, file):
        return ("suse", 1, 1)

    def get_datestamp(self):
        base = self.get_rootdir()
        if os.path.exists("%s/.discinfo" % base):
            discinfo = open("%s/.discinfo" % base, "r")
            datestamp = discinfo.read().split("\n")[0]
            discinfo.close()
        else:
            return 0
        return float(datestamp)

    def set_variance(self, flavor, major, minor, arch):

        os_version = "suse"

        kickbase = "/var/lib/cobbler/kickstarts"
        return os_version, "autoyast_sample.xml"

# ==========================================================================

def get_import_manager(config,logger):
    return ImportSuseManager(config,logger)

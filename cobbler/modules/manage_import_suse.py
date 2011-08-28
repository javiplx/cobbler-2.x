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


from manage_import_base import ImportBaseManager

class ImportSuseManager ( ImportBaseManager ) :

    __breed_str__ = "suse"

    signatures = [
          'suse'
       ]

    # required function for import modules
    def get_valid_arches(self):
        return ["i386", "ia64", "ppc", "ppc64", "s390", "s390x", "x86_64", "x86",]

    # required function for import modules
    def get_valid_breeds(self):
        return ["suse",]

    # required function for import modules
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

    def get_install_tree(self, distro, base):
            dest_link = os.path.join(self.settings.webdir, "links", distro.name)
            # create the links directory only if we are mirroring because with
            # SELinux Apache can't symlink to NFS (without some doing)
            if not os.path.exists(dest_link):
                try:
                    # NOTE : only difference respect to the get_install_tree on RedHat importer
                    os.symlink(base + "-" + distro.arch, dest_link)
                except:
                    # this shouldn't happen but I've seen it ... debug ...
                    self.logger.warning("symlink creation failed: %s, %s" % (base,dest_link) )
            # how we set the tree depends on whether an explicit network_root was specified
            return "http://@@http_server@@/cblr/links/%s" % distro.name

    def repo_scanner(self,distro,dirname,fnames):
        """
        This is an os.path.walk routine that looks for potential yum repositories
        to be added to the configuration for post-install usage.
        """

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
        # NOTE : this methods allows to use the same repo_scanner than RedHat importer
        pass

    def valid_initrd(self,name):
        if name.startswith("initrd") or name.startswith("ramdisk.image.gz") :
            return True
        return False

    def valid_kernel(self,name):
        if name.startswith("vmlinu") or name.startswith("kernel.img") or name.startswith("linux") :
            return True
        return False

    def add_entry(self,dirname,kernel,initrd):
        """
        When we find a directory with a valid kernel/initrd in it, create the distribution objects
        as appropriate and save them.  This includes creating xen and rescue distros/profiles
        if possible.
        """

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
                utils.die(self.logger, "Given arch (%s) not found on imported tree %s"%(self.arch,self.get_pkgdir()))
        if proposed_arch:
            if archs and proposed_arch not in archs:
                self.logger.warning("arch from pathname (%s) not found on imported tree %s" % (proposed_arch,self.get_pkgdir()))
                return

            archs = [ proposed_arch ]

        if len(archs)>1:
            if self.breed in [ "suse" ]:
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

            if name.find("-autoboot") != -1:
                # this is an artifact of some EL-3 imports
                continue

            distro.set_name(name)
            distro.set_kernel(kernel)
            distro.set_initrd(initrd)
            distro.set_arch(pxe_arch)
            distro.set_breed(self.breed)
            # NOTE : only difference with the distro_adder from base class
            distro.set_kernel_options("install=http://@@http_server@@/cblr/links/%s" % (name))
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
                #FIXME: The created profile holds a default kickstart, and should be breed specific
                profile = self.config.new_profile()
            else:
                self.logger.info("skipping existing profile, name already exists: %s" % name)
                continue

            # save our minimal profile which just points to the distribution and a good
            # default answer file

            profile.set_name(name)
            profile.set_distro(name)
            profile.set_kickstart(self.kickstart_file)

            # depending on the name of the profile we can define a good virt-type
            # for usage with koan

            if name.find("-xen") != -1:
                profile.set_virt_type("xenpv")
            elif name.find("vmware") != -1:
                profile.set_virt_type("vmware")
            else:
                profile.set_virt_type("qemu")

            # save our new profile to the collection

            self.profiles.add(profile,save=True)

            # Create a rescue image as well, if this is not a xen distro
            # but only for red hat profiles

        return distros_added

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

    def get_rootdir(self):
        return self.rootdir

    def match_kernelarch_file(self, filename):
        """
        Is the given filename a kernel filename?
        """

        if not filename.endswith("rpm") and not filename.endswith("deb"):
            return False
        for match in ["kernel-header", "kernel-source", "kernel-smp", "kernel-default", "kernel-desktop", "linux-headers-", "kernel-devel", "kernel-"]:
            if filename.find(match) != -1:
                return True
        return False

    def scan_pkg_filename(self, rpm):
        """
        Determine what the distro is based on the release package filename.
        """

        rpm = os.path.basename(rpm)

        return ("suse", 1, 1)

    def get_datestamp(self):
        """
        Based on a RedHat tree find the creation timestamp
        """
        base = self.get_rootdir()
        if os.path.exists("%s/.discinfo" % base):
            discinfo = open("%s/.discinfo" % base, "r")
            datestamp = discinfo.read().split("\n")[0]
            discinfo.close()
        else:
            return 0
        return float(datestamp)

    def set_variance(self, flavor, major, minor, arch):
        """
        find the profile kickstart and set the distro breed/os-version based on what
        we can find out from the rpm filenames and then return the kickstart
        path to use.
        """

        os_version = "suse"

        kickbase = "/var/lib/cobbler/kickstarts"
        return os_version, "autoyast_sample.xml"

# ==========================================================================

def get_import_manager(config,logger):
    return ImportSuseManager(config,logger)

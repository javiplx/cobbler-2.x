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

class ImportRedhatManager ( ImportManagerBase ) :

    __breed__ = "redhat"

    signatures = [
          'RedHat/RPMS',
          'RedHat/rpms',
          'RedHat/Base',
          'Fedora/RPMS',
          'Fedora/rpms',
          'CentOS/RPMS',
          'CentOS/rpms',
          'CentOS',
          'Packages',
          'Fedora',
          'Server',
          'Client',
          'SL',
       ]

    def get_valid_arches(self):
        return ["i386", "ia64", "ppc", "ppc64", "s390", "s390x", "x86_64", "x86",]

    def get_valid_breeds(self):
        return ["redhat",]

    def get_valid_os_versions(self):
        return ["rhel2.1", "rhel3", "rhel4", "rhel5", "rhel6", 
                "fedora5", "fedora6", "fedora7", "fedora8", "fedora9", "fedora10", 
                "fedora11", "fedora12", "fedora13", "fedora14", "fedora15",
                "generic24", "generic26", "virtio26", "other",]

    def get_valid_repo_breeds(self):
        return ["rsync", "rhn", "yum",]

    def get_release_files(self):
        data = glob.glob(os.path.join(self.get_pkgdir(), "*release-*"))
        data2 = []
        for x in data:
            b = os.path.basename(x)
            if b.find("fedora") != -1 or \
               b.find("redhat") != -1 or \
               b.find("centos") != -1:
                data2.append(x)
        return data2

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

    def is_initrd(self,filename):
        if ( filename.startswith("initrd") or filename.startswith("ramdisk.image.gz") ) and filename != "initrd.size":
            return True
        return False

    def is_kernel(self,filename):
        if ( filename.startswith("vmlinu") or filename.startswith("kernel.img") or filename.startswith("linux") ) and filename.find("initrd") == -1:
            return True
        return False

    def distro_adder(self,distros_added,dirname,fnames):
        """
        This is an os.path.walk routine that finds distributions in the directory
        to be scanned and then creates them.
        """

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

    def get_name_from_dirname(self,dirname):
        return self.mirror_name

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

    def scan_pkg_filename(self, file):
        """
        Determine what the distro is based on the release package filename.
        """

        file = os.path.basename(file)

        # if it looks like a RHEL RPM we'll cheat.
        # it may be slightly wrong, but it will be close enough
        # for RHEL5 we can get it exactly.

        for x in [ "4AS", "4ES", "4WS", "4common", "4Desktop" ]:
            if file.find(x) != -1:
                return ("redhat", 4, 0)
        for x in [ "3AS", "3ES", "3WS", "3Desktop" ]:
            if file.find(x) != -1:
                return ("redhat", 3, 0)
        for x in [ "2AS", "2ES", "2WS", "2Desktop" ]:
            if file.find(x) != -1:
                return ("redhat", 2, 0)

        # now get the flavor:
        flavor = "redhat"
        if file.lower().find("fedora") != -1:
            flavor = "fedora"
        if file.lower().find("centos") != -1:
            flavor = "centos"

        # get all the tokens and try to guess a version
        accum = []
        tokens = file.split(".")
        for t in tokens:
            tokens2 = t.split("-")
            for t2 in tokens2:
                try:
                    float(t2)
                    accum.append(t2)
                except:
                    pass

        major = float(accum[0])
        minor = float(accum[1])
        return (flavor, major, minor)

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

        if flavor == "fedora":

            # this may actually fail because the libvirt/virtinst database
            # is not always up to date.  We keep a simplified copy of this
            # in codes.py.  If it fails we set it to something generic
            # and don't worry about it.

            try:
                os_version = "fedora%s" % int(major)
            except:
                os_version = "other"

        if flavor == "redhat" or flavor == "centos":

            if major <= 2:
                # rhel2.1 is the only rhel2
                os_version = "rhel2.1"
            else:
                try:
                    # must use libvirt version
                    os_version = "rhel%s" % (int(major))
                except:
                    os_version = "other"

        kickbase = "/var/lib/cobbler/kickstarts"
        # Look for ARCH/OS_VERSION.MINOR kickstart first
        #          ARCH/OS_VERSION next
        #          OS_VERSION next
        #          OS_VERSION.MINOR next
        #          ARCH/default.ks next
        #          FLAVOR.ks next
        kickstarts = [
            "%s/%s/%s.%i.ks" % (kickbase,arch,os_version,int(minor)),
            "%s/%s/%s.ks" % (kickbase,arch,os_version),
            "%s/%s.%i.ks" % (kickbase,os_version,int(minor)),
            "%s/%s.ks" % (kickbase,os_version),
            "%s/%s/default.ks" % (kickbase,arch),
            "%s/%s.ks" % (kickbase,flavor),
        ]
        for kickstart in kickstarts:
            if os.path.exists(kickstart):
                return os_version, kickstart

        major = int(major)

        if flavor == "fedora":
            if major >= 8:
                return os_version , "/var/lib/cobbler/kickstarts/sample_end.ks"
            if major >= 6:
                return os_version , "/var/lib/cobbler/kickstarts/sample.ks"

        if flavor == "redhat" or flavor == "centos":
            if major >= 5:
                return os_version , "/var/lib/cobbler/kickstarts/sample.ks"

            return os_version , "/var/lib/cobbler/kickstarts/legacy.ks"

        self.logger.warning("could not use distro specifics, using rhel 4 compatible kickstart")
        return None , "/var/lib/cobbler/kickstarts/legacy.ks"

# ==========================================================================

def get_import_manager(config,logger):
    return ImportRedhatManager(config,logger)

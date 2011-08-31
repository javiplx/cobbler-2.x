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

class ImportDebianUbuntuManager ( ImportManagerBase ) :

    __breed__ = "debian_ubuntu"

    signatures = [
           'pool',
       ]

    def get_valid_arches(self):
        return ["i386", "ppc", "x86_64", "x86",]

    def get_valid_breeds(self):
        return ["debian","ubuntu"]

    def get_valid_os_versions(self):
        if self.breed == "debian":
            return ["etch", "lenny", "squeeze", "sid", "stable", "testing", "unstable", "experimental",]
        elif self.breed == "ubuntu":
            return ["dapper", "hardy", "karmic", "lucid", "maverick", "natty",]
        else:
            return []

    def get_valid_repo_breeds(self):
        return ["apt",]

    def get_release_files(self):
        """
        Find distro release packages.
        """
        return glob.glob(os.path.join(self.get_rootdir(), "dists/*"))

    def set_breed_from_self(self):
        for breed in self.get_valid_breeds():
            # NOTE : Although we break the loop after the first match,
            # multiple debian derived distros can actually live at the same pool -- JP
            d = os.path.join(self.mirror, breed)
            if (os.path.islink(d) and os.path.isdir(d) and os.path.realpath(d) == os.path.realpath(self.mirror)) or os.path.basename(self.mirror) == breed:
                self.breed = breed
                break
        if not self.breed:
            utils.die(self.logger,"import failed - could not determine breed of %s-based distro"%self.__breed__)

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
                for file in self.get_release_files():
                    results = self.scan_pkg_filename(file)
                    # FIXME : If os is not found on tree but set with CLI, no kickstart is searched
                    if results is None:
                        self.logger.warning("skipping %s" % file)
                        continue
                    (flavor, major, minor, release) = results
                    # Why use set_variance()? scan_pkg_filename() does everything we need now - jcammarata
                    #version , ks = self.set_variance(flavor, major, minor, distro.arch)
                    if self.os_version:
                        if self.os_version != flavor:
                            utils.die(self.logger,"CLI version differs from tree : %s vs. %s" % (self.os_version,flavor))
                    distro.set_comment("%s %s (%s.%s.%s) %s" % (self.breed,flavor,major,minor,release,self.arch))
                    distro.set_os_version(flavor)
                    # is this even valid for debian/ubuntu? - jcammarata
                    #ds = self.get_datestamp()
                    #if ds is not None:
                    #    distro.set_tree_build_time(ds)
                    profile.set_kickstart("/var/lib/cobbler/kickstarts/sample.seed")
                    self.profiles.add(profile,save=True)

            self.configure_tree_location(distro)
            self.distros.add(distro,save=True) # re-save
            self.api.serialize()

    def get_local_tree(self, distro):
        dists_path = os.path.join( self.path , "dists" )
        if os.path.isdir( dists_path ):
            return "http://@@http_server@@/cblr/ks_mirror/%s" % (self.mirror_name)
        else:
            return "http://@@http_server@@/cblr/repo_mirror/%s" % (distro.name)

    def get_rootdir(self):
        return self.mirror

    def match_kernelarch_file(self, filename):
        """
        Is the given filename a kernel filename?
        """
        if not filename.endswith("deb"):
            return False
        if filename.startswith("linux-headers-"):
            return True
        return False

    def scan_pkg_filename(self, file):
        """
        Determine what the distro is based on the release package filename.
        """
        # FIXME: all of these dist_names should probably be put in a function
        # which would be called in place of looking in codes.py.  Right now
        # you have to update both codes.py and this to add a new release
        if self.breed == "debian":
            dist_names = ['etch','lenny',]
        elif self.breed == "ubuntu":
            dist_names = ['dapper','hardy','intrepid','jaunty','karmic','lynx','maverick','natty',]
        else:
            return None

        if os.path.basename(file) in dist_names:
            release_file = os.path.join(file,'Release')
            self.logger.info("Found %s release file: %s" % (self.breed,release_file))

            f = open(release_file,'r')
            lines = f.readlines()
            f.close()

            for line in lines:
                if line.lower().startswith('version: '):
                    version = line.split(':')[1].strip()
                    values = version.split('.')
                    if len(values) == 1:
                        # I don't think you'd ever hit this currently with debian or ubuntu,
                        # just including it for safety reasons
                        return (os.path.basename(file), values[0], "0", "0")
                    elif len(values) == 2:
                        return (os.path.basename(file), values[0], values[1], "0")
                    elif len(values) > 2:
                        return (os.path.basename(file), values[0], values[1], values[2])
        return None

    def get_datestamp(self):
        """
        Not used for debian/ubuntu... should probably be removed? - jcammarata
        """
        pass

    def set_variance(self, flavor, major, minor, arch):
        """
        Set distro specific versioning.
        """
        pass

    def repo_scanner(self,distro,dirname,fnames):
      """Called as os.path.walk handler, but directories removed to disable recursion.
If no 'dists' is found at toplevel, a netboot import is assumed and
standard & security repositories are added."""

      if "dists" not in fnames :

        repodata = { 'breed':"apt" , 'arch':distro.arch , 'keep_updated':False , 'mirror_locally':False }

        repo = item_repo.Repo.Factory(self.config,repodata)
        repo.yumopts["--ignore-release-gpg"] = ""
        repo.yumopts["--verbose"] = ""
        repo.set_name( distro.name )
        repo.set_os_version( distro.os_version )
        # NOTE : The location of the mirror should come from timezone
        repo.set_mirror( "http://ftp.%s.debian.org/debian/dists/%s" % ( 'us' , '@@suite@@' ) )

        security_repo = item_repo.Repo.Factory(self.config,repodata)
        security_repo.yumopts["--ignore-release-gpg"] = ""
        security_repo.yumopts["--verbose"] = ""
        security_repo.set_name( distro.name + "-security" )
        security_repo.set_os_version( distro.os_version )
        # There are no official mirrors for security updates
        security_repo.set_mirror( "http://security.debian.org/debian-security/dists/%s/updates" % '@@suite@@' )

        self.logger.info("Added repos for %s" % distro.name)
        repos  = self.config.repos()
        repos.add(repo,save=True)
        repos.add(security_repo,save=True)

      for x in list(fnames):
          if os.path.isdir( os.path.join(dirname,x) ):
              fnames.remove( x )

# ==========================================================================

def get_import_manager(config,logger):
    return ImportDebianUbuntuManager(config,logger)

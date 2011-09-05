"""
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

class NotImplemented ( Exception ) :

    def __init__(self,str,obj,method=None):
        prefix = "%s" % obj
        if method :
            prefix += ".%s()" % method
        Exception.__init__(self,"%s %s"%(prefix,str))

class ImportManagerBase:

    __breed__ = None

    signatures = ()

        # If no breed was specified on the command line, set it to "redhat" for this module
    def set_breed_from_self(self):
        """Called if no breed given and sets breed from values object variables.
Needs to be overriden if a single importer can be used for multple breeds"""
        self.breed = self.__breed__

    def get_valid_arches(self):
        raise NotImplemented( "needs to be implemented" , self , "get_valid_arches" )

    def get_valid_breeds(self):
        raise NotImplemented( "needs to be implemented" , self , "get_valid_breeds" )

    # NOTE : get_valid_os_versions get_valid_repo_breeds get_release_files
    #   are never used within base class

    def get_valid_os_versions(self):
        raise NotImplemented( "needs to be implemented" , self , "get_valid_os_versions" )

    def get_valid_repo_breeds(self):
        raise NotImplemented( "needs to be implemented" , self , "get_valid_repo_breeds" )

    def get_release_files(self):
        """
        Find distro release packages.
        """
        raise NotImplemented( "needs to be implemented" , self , "get_release_files" )

    def is_initrd(self,filename):
        """Decides wether given file has a valid initrd name"""
        raise NotImplemented( "needs to be implemented" , self , "is_initrd" )

    def is_kernel(self,filename):
        """Decides wether given file has a valid kernel name"""
        raise NotImplemented( "needs to be implemented" , self , "is_kernel" )

    def get_name_from_dirname(self,dirname):
        """Used by get_proposed_name for the initial guess if no network root"""
        raise NotImplemented( "needs to be implemented" , self , "get_name_from_dirname" )

    def get_local_tree(self, distro):
        """Used by configure_tree_location to find the tree if no network root"""
        raise NotImplemented( "needs to be implemented" , self , "get_local_tree" )

    def get_rootdir(self):
        raise NotImplemented( "needs to be implemented" , self , "get_rootdir" )

    # NOTE : besides the functions above also repo_scanner, match_kernelarch_file

    def __init__(self,config,logger):
        """
        Constructor
        """
        self.logger        = logger
        self.config        = config
        self.api           = config.api
        self.distros       = config.distros()
        self.profiles      = config.profiles()
        self.systems       = config.systems()
        self.settings      = config.settings()
        self.repos         = config.repos()
        self.templar       = templar.Templar(config)
        if not self.__breed__:
            raise NotImplemented( "__breed__ must be defined on derived classes" )

    # required function for import modules
    def what(self):
        return "import/%s" % self.__breed__

    # required function for import modules
    def check_for_signature(self,path,cli_breed):
       self.logger.info("scanning %s for a %s-based distro signature" % (path,self.__breed__))
       for signature in self.signatures:
           d = os.path.join(path,signature)
           if os.path.exists(d):
               self.logger.info("Found a %s compatible signature: %s" % (self.__breed__,signature))
               return (True,signature)

       if cli_breed and cli_breed in self.get_valid_breeds():
           self.logger.info("Warning: No distro signature for kernel at %s, using value from command line" % path)
           return (True,None)

       return (False,None)

    # required function for import modules
    def run(self,pkgdir,mirror,mirror_name,network_root=None,kickstart_file=None,rsync_flags=None,arch=None,breed=None,os_version=None):
        self.pkgdir = pkgdir
        self.mirror = mirror
        self.mirror_name = mirror_name
        self.network_root = network_root
        self.kickstart_file = kickstart_file
        self.rsync_flags = rsync_flags
        self.arch = arch
        self.breed = breed
        self.os_version = os_version

        # some fixups for the XMLRPC interface, which does not use "None"
        if self.arch == "":           self.arch           = None
        if self.mirror == "":         self.mirror         = None
        if self.mirror_name == "":    self.mirror_name    = None
        if self.kickstart_file == "": self.kickstart_file = None
        if self.os_version == "":     self.os_version     = None
        if self.rsync_flags == "":    self.rsync_flags    = None
        if self.network_root == "":   self.network_root   = None

        if self.breed == None:
            self.set_breed_from_self()

        # debug log stuff for testing
        #self.logger.info("self.pkgdir = %s" % str(self.pkgdir))
        #self.logger.info("self.mirror = %s" % str(self.mirror))
        #self.logger.info("self.mirror_name = %s" % str(self.mirror_name))
        #self.logger.info("self.network_root = %s" % str(self.network_root))
        #self.logger.info("self.kickstart_file = %s" % str(self.kickstart_file))
        #self.logger.info("self.rsync_flags = %s" % str(self.rsync_flags))
        #self.logger.info("self.arch = %s" % str(self.arch))
        #self.logger.info("self.breed = %s" % str(self.breed))
        #self.logger.info("self.os_version = %s" % str(self.os_version))

        # both --import and --name are required arguments

        if self.mirror is None:
            utils.die(self.logger,"import failed.  no --path specified")
        if self.mirror_name is None:
            utils.die(self.logger,"import failed.  no --name specified")

        # if --arch is supplied, validate it to ensure it's valid

        if self.arch is not None and self.arch != "":
            self.arch = self.arch.lower()
            if self.arch in ( 'x86' , 'i486', 'i586', 'i686' ):
                # be consistent
                self.arch = "i386"
            if self.arch not in self.get_valid_arches():
                utils.die(self.logger,"arch must be one of: %s" % string.join(self.get_valid_arches(),", "))

        # if we're going to do any copying, set where to put things
        # and then make sure nothing is already there.

        self.path = os.path.normpath( "%s/ks_mirror/%s" % (self.settings.webdir, self.mirror_name) )
        self.rootdir = os.path.normpath( "%s/ks_mirror/%s" % (self.settings.webdir, self.mirror_name) )
        if os.path.exists(self.path) and self.arch is None:
            # FIXME : Raise exception even when network_root is given ?
            utils.die(self.logger,"Something already exists at this import location (%s).  You must specify --arch to avoid potentially overwriting existing files." % self.path)

        # import takes a --kickstart for forcing selection that can't be used in all circumstances

        if self.kickstart_file and not self.breed:
            utils.die(self.logger,"Kickstart file can only be specified when a specific breed is selected")

        if self.os_version and not self.breed:
            utils.die(self.logger,"OS version can only be specified when a specific breed is selected")

        if self.breed and self.breed.lower() not in self.get_valid_breeds():
            utils.die(self.logger,"Supplied import breed is not supported by this module")

        # if --arch is supplied, make sure the user is not importing a path with a different
        # arch, which would just be silly.

        if self.arch:
            # append the arch path to the name if the arch is not already
            # found in the name.
            for x in self.get_valid_arches():
                if self.path.lower().find(x) != -1:
                    if self.arch != x :
                        utils.die(self.logger,"Architecture found on pathname (%s) does not fit the one given in command line (%s)"%(x,self.arch))
                    break
            else:
                # FIXME : This is very likely removed later at get_proposed_name, and the guessed arch appended again
                self.path += ("-%s" % self.arch)

        # make the output path and mirror content but only if not specifying that a network
        # accessible support location already exists (this is --available-as on the command line)

        if self.network_root is None:
            # we need to mirror (copy) the files

            utils.mkdir(self.path)

            if self.mirror.startswith("http://") or self.mirror.startswith("ftp://") or self.mirror.startswith("nfs://"):

                # http mirrors are kind of primative.  rsync is better.
                # that's why this isn't documented in the manpage and we don't support them.
                # TODO: how about adding recursive FTP as an option?

                utils.die(self.logger,"unsupported protocol")

            else:

                # good, we're going to use rsync..
                # we don't use SSH for public mirrors and local files.
                # presence of user@host syntax means use SSH

                # kick off the rsync now

                if not utils.rsync_files(self.mirror, self.path, self.rsync_flags, self.logger, False):
                    utils.die(self.logger, "failed to rsync the files")

        else:

            # rather than mirroring, we're going to assume the path is available
            # over http, ftp, and nfs, perhaps on an external filer.  scanning still requires
            # --mirror is a filesystem path, but --available-as marks the network path

            if not os.path.exists(self.mirror):
                utils.die(self.logger, "path does not exist: %s" % self.mirror)

            # find the filesystem part of the path, after the server bits, as each distro
            # URL needs to be calculated relative to this.

            if not self.network_root.endswith("/"):
                self.network_root = self.network_root + "/"
            self.path = os.path.normpath( self.mirror )
            valid_roots = [ "nfs://", "ftp://", "http://" ]
            for valid_root in valid_roots:
                if self.network_root.startswith(valid_root):
                    break
            else:
                utils.die(self.logger, "Network root given to --available-as must be nfs://, ftp://, or http://")
            if self.network_root.startswith("nfs://"):
                try:
                    (a,b,rest) = self.network_root.split(":",3)
                except:
                    utils.die(self.logger, "Network root given to --available-as is missing a colon, please see the manpage example.")

        # now walk the filesystem looking for distributions that match certain patterns

        self.logger.info("adding distros")
        distros_added = []
        # FIXME : search below self.path for isolinux configurations or known directories from TRY_LIST
        os.path.walk(self.path, self.distro_adder, distros_added)

        # find out if we can auto-create any repository records from the install tree

        if self.network_root is None:
            self.logger.info("associating repos")
            # FIXME: this automagic is not possible (yet) without mirroring
            self.repo_finder(distros_added)

        # find the most appropriate answer files for each profile object

        self.logger.info("associating kickstarts")
        self.kickstart_finder(distros_added)

        # ensure bootloaders are present
        self.api.pxegen.copy_bootloaders()

        return True

    def repo_finder(self, distros_added):
        """
        This routine looks through all distributions and tries to find
        any applicable repositories in those distributions for post-install
        usage.
        """

        for distro in distros_added:
            self.logger.info("traversing distro %s" % distro.name)
            # FIXME : Shouldn't decide this the value of self.network_root ?
            if distro.kernel.find("ks_mirror") != -1:
                basepath = os.path.dirname(distro.kernel)
                top = self.get_rootdir()
                self.logger.info("descent into %s" % top)
                # FIXME : The location of repo definition is known from breed
                os.path.walk(top, self.repo_scanner, distro)
            else:
                self.logger.info("this distro isn't mirrored")

    def repo_scanner(self,distro,dirname,fnames):
        """
        This is an os.path.walk routine that looks for potential yum repositories
        to be added to the configuration for post-install usage.
        """
        raise NotImplemented( "needs to be implemented" , self , "repo_scanner" )

    # NOTE : redhat and suse diverge because they account for PAE images,
    #   but mainly because distro is not added if directory is 'isolinux'
    def distro_adder(self,distros_added,dirname,fnames):
        """
        This is an os.path.walk routine that finds distributions in the directory
        to be scanned and then creates them.
        """

        # FIXME: If there are more than one kernel or initrd image on the same directory,
        # results are unpredictable

        initrd = None
        kernel = None

        for x in fnames:
            adtls = []

            fullname = os.path.join(dirname,x)
            if os.path.islink(fullname) and os.path.isdir(fullname):
                if fullname.startswith(self.path):
                    self.logger.warning("avoiding symlink loop")
                    continue
                self.logger.info("following symlink: %s" % fullname)
                os.path.walk(fullname, self.distro_adder, distros_added)

            if self.is_initrd(x):
                initrd = os.path.join(dirname,x)
            if self.is_kernel(x):
                kernel = os.path.join(dirname,x)

            # if we've collected a matching kernel and initrd pair, turn the in and add them to the list
            if initrd is not None and kernel is not None:
                adtls.append(self.add_entry(dirname,kernel,initrd))
                kernel = None
                initrd = None

            for adtl in adtls:
                distros_added.extend(adtl)

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

            if name.find("-autoboot") != -1:
                # this is an artifact of some EL-3 imports
                continue

            distro.set_name(name)
            distro.set_kernel(kernel)
            distro.set_initrd(initrd)
            distro.set_arch(pxe_arch)
            distro.set_breed(self.breed)
            if self.breed == "suse" :
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

            # depending on the name of the profile and the breed we can define
            # a good virt-type for usage with koan

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
        """
        Given a directory name where we have a kernel/initrd pair, try to autoname
        the distribution (and profile) object based on the contents of that path
        """

        if self.network_root is not None:
            name = self.get_name_from_dirname(dirname)
        else:
            # remove the part that says /var/www/cobbler/ks_mirror/name
            name = "-".join(dirname.split("/")[5:])

        if kernel is not None and kernel.find("PAE") != -1:
            name = name + "-PAE"

        # These are all Ubuntu's doing, the netboot images are buried pretty
        # deep. ;-) -JC
        name = name.replace("-netboot","")
        name = name.replace("-ubuntu-installer","")
        name = name.replace("-amd64","")
        name = name.replace("-i386","")

        # we know that some kernel paths should not be in the name

        name = name.replace("-images","")
        name = name.replace("-pxeboot","")
        name = name.replace("-install","")
        name = name.replace("-isolinux","")

        # some paths above the media root may have extra path segments we want
        # to clean up

        name = name.replace("-os","")
        name = name.replace("-tree","")
        name = name.replace("var-www-cobbler-", "")
        name = name.replace("ks_mirror-","")
        name = name.replace("--","-")

        # remove any architecture name related string, as real arch will be appended later

        name = name.replace("chrp","ppc64")

        for separator in [ '-' , '_'  , '.' ] :
            for arch in [ "i386" , "x86_64" , "ia64" , "ppc64", "ppc32", "ppc", "x86" , "s390x", "s390" , "386" , "amd" ]:
                name = name.replace("%s%s" % ( separator , arch ),"")

        return name

    def get_proposed_arch(self,dirname):
        """
        Given an directory name, can we infer an architecture from a path segment?
        """
        if dirname.find("x86_64") != -1 or dirname.find("amd") != -1:
            return "x86_64"
        if dirname.find("ia64") != -1:
            return "ia64"
        if dirname.find("i386") != -1 or dirname.find("386") != -1 or dirname.find("x86") != -1:
            return "i386"
        if dirname.find("s390x") != -1:
            return "s390x"
        if dirname.find("s390") != -1:
            return "s390"
        if dirname.find("ppc64") != -1 or dirname.find("chrp") != -1:
            return "ppc64"
        if dirname.find("ppc32") != -1:
            return "ppc"
        if dirname.find("ppc") != -1:
            return "ppc"
        return None

    def arch_walker(self,foo,dirname,fnames):
        """
        See docs on learn_arch_from_tree.

        The TRY_LIST is used to speed up search, and should be dropped for default importer
        Searched kernel names are kernel-header, linux-headers-, kernel-largesmp, kernel-hugemem

        This method is useful to get the archs, but also to package type and a raw guess of the breed
        """

        # try to find a kernel header RPM and then look at it's arch.
        for x in fnames:
            if self.match_kernelarch_file(x):
                for arch in self.get_valid_arches():
                    if x.find(arch) != -1:
                        foo[arch] = 1
                for arch in [ "i686" , "amd64" ]:
                    if x.find(arch) != -1:
                        foo[arch] = 1

    def match_kernelarch_file(self, filename):
        """
        Is the given filename a kernel filename?
        """
        raise NotImplemented( "needs to be implemented" , self , "match_kernelarch_file" )

    def kickstart_finder(self,distros_added):
        """
        For all of the profiles in the config w/o a kickstart, use the
        given kickstart file, or look at the kernel path, from that,
        see if we can guess the distro, and if we can, assign a kickstart
        if one is available for it.
        """
        raise NotImplemented( "needs to be implemented" , self , "kickstart_finder" )

    def configure_tree_location(self, distro):
        """
        Once a distribution is identified, find the part of the distribution
        that has the URL in it that we want to use for kickstarting the
        distribution, and create a ksmeta variable $tree that contains this.
        """

        base = self.get_rootdir()

        if self.network_root is None:
            tree = self.get_local_tree(distro)
        else:
            # where we assign the kickstart source is relative to our current directory
            # and the input start directory in the crawl.  We find the path segments
            # between and tack them on the network source path to find the explicit
            # network path to the distro that Anaconda can digest.
            tail = utils.path_tail(self.path, base)
            tree = self.network_root[:-1] + tail
        self.set_install_tree(distro, tree)

    def get_pkgdir(self):
        if not self.pkgdir:
            return None
        return os.path.join(self.get_rootdir(),self.pkgdir)

    def set_install_tree(self, distro, url):
        distro.ks_meta["tree"] = url

    def learn_arch_from_tree(self):
        """
        If a distribution is imported from DVD, there is a good chance the path doesn't
        contain the arch and we should add it back in so that it's part of the
        meaningful name ... so this code helps figure out the arch name.  This is important
        for producing predictable distro names (and profile names) from differing import sources
        """
        result = {}
        # FIXME : this is called only once, should not be a walk
        if self.get_pkgdir():
            os.path.walk(self.get_pkgdir(), self.arch_walker, result)
        if result.pop("amd64",False):
            result["x86_64"] = 1
        if result.pop("i686",False):
            result["i386"] = 1
        return result.keys()

    # NOTE : the method below is not used in base class but on common
    #   implementations of kickstart_finder()

    def scan_pkg_filename(self, file):
        """
        Determine what the distro is based on the release package filename.
        """
        raise NotImplemented( "needs to be implemented" , self , "scan_pkg_filename" )

    # NOTE : methods below are only used in RedHat alike kickstart_finder implementations

    def get_datestamp(self):
        """
        Based on a RedHat tree find the creation timestamp
        """
        raise NotImplemented( "needs to be implemented" , self , "get_datestamp" )

    def set_variance(self, flavor, major, minor, arch):
        """
        find the profile kickstart and set the distro breed/os-version based on what
        we can find out from the rpm filenames and then return the kickstart
        path to use.
        """
        raise NotImplemented( "needs to be implemented" , self , "set_variance" )

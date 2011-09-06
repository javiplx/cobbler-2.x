"""
Builds out and synchronizes yum repo mirrors.
Initial support for rsync, perhaps reposync coming later.

Copyright 2006-2007, Red Hat, Inc
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

import os
import os.path
import time
import yaml # Howell-Clark version
import sys
HAS_YUM = True
try:
    import yum
except:
    HAS_YUM = False

import utils
from cexceptions import *
import traceback
import errno
from utils import _
import clogger

class RepoSync:
    """
    Handles conversion of internal state to the tftpboot tree layout
    """

    # ==================================================================================

    def __init__(self,config,tries=1,nofail=False,logger=None):
        """
        Constructor
        """
        self.verbose   = True
        self.config    = config
        self.distros   = config.distros()
        self.profiles  = config.profiles()
        self.systems   = config.systems()
        self.settings  = config.settings()
        self.repos     = config.repos()
        self.tries     = tries
        self.nofail    = nofail
        self.logger    = logger

        if logger is None:
           self.logger = clogger.Logger()

        self.logger.info("hello, reposync")


    # ===================================================================

    def run(self, name=None, verbose=True):
        """
        Syncs the current repo configuration file with the filesystem.
        """
            
        self.logger.info("run, reposync, run!")
        
        try:
            self.tries = int(self.tries)
        except:
            utils.die(self.logger,"retry value must be an integer")

        self.verbose = verbose

        report_failure = False
        for repo in self.repos:

            env = repo.environment

            for k in env.keys():
                self.logger.info("environment: %s=%s" % (k,env[k]))
                if env[k] is not None:
                    os.putenv(k,env[k])

            if name is not None and repo.name != name:
                # invoked to sync only a specific repo, this is not the one
                continue
            elif not repo.keep_updated:
                # invoked to run against all repos, but this one is off
                self.logger.info("%s is set to not be updated" % repo.name)
                continue

            if not repo.mirror_locally:
                utils.die(self.logger,"Cannot sync repo %s, not marked as local mirror" % repo.name)

            repo_mirror = os.path.join(self.settings.webdir, "repo_mirror")
            repo_path = os.path.join(repo_mirror, repo.name)
            mirror = repo.mirror

            if not os.path.isdir(repo_path) and not repo.mirror.lower().startswith("rhn://"):
                os.makedirs(repo_path)
            
            # which may actually NOT reposync if the repo is set to not mirror locally
            # but that's a technicality

            for x in range(self.tries+1,1,-1):
                success = False
                try:
                    repo.sync(self.logger) 
                    success = True
                except:
                    utils.log_exc(self.logger)
                    self.logger.warning("reposync failed, tries left: %s" % (x-2))

            if not success:
                report_failure = True
                if not self.nofail:
                    utils.die(self.logger,"reposync failed, retry limit reached, aborting")
                else:
                    self.logger.error("reposync failed, retry limit reached, skipping")

            self.update_permissions(repo_path)

        if report_failure:
            utils.die(self.logger,"overall reposync failed, at least one repo failed to synchronize")

        return True

    # ====================================================================================

    def update_permissions(self, repo_path):
        """
        Verifies that permissions and contexts after an rsync are as expected.
        Sending proper rsync flags should prevent the need for this, though this is largely
        a safeguard.
        """
        # all_path = os.path.join(repo_path, "*")
        cmd1 = "chown -R root:apache %s" % repo_path
        utils.subprocess_call(self.logger, cmd1)

        cmd2 = "chmod -R 755 %s" % repo_path
        utils.subprocess_call(self.logger, cmd2)


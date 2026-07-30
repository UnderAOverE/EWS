#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : common/logger.py.                                                                  #
# Date of birth : 2026-07-30.                                                                         #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Local mirror of the host's shared application logger. Modules import ``logger``      #
#                 from here (``from src.common.logger import logger``) instead of each defining their   #
#                 own ``logging.getLogger(__name__)``. Replaced by the host's logger at merge.        #
# Dependencies  : Standard library only (logging, sys).                                               #
# Modifications : 2026-07-30 Shane Reddy — Initial version.                                            #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

import logging

# Internal imports

# Local variables

# The single shared application logger. Standard logging.Logger — supports info/debug/warning/error
# and the extra={...} pattern the host uses. The host's real logger (handlers, formatting, level)
# replaces this at merge; here it is just a named logger the standard config picks up.
logger: logging.Logger = logging.getLogger("amp")


# end_common/logger.py

#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : common/__init__.py.                                                                #
# Date of birth : 2026-07-30.                                                                         #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Local mirror of the host application's src/common package (logger, db base repos,    #
#                 utils, constants). Present here only so the zelle bounded context is testable        #
#                 standalone; the host's real common package replaces it at merge time.               #
# Dependencies  : Standard library only (sys).                                                        #
# Modifications : 2026-07-30 Shane Reddy — Initial version.                                            #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


"""
Local mirror of the host application's ``src/common`` package. Replaced by the host's real common
package once the zelle context is merged into ``fdn-c-amp-fapis-py``.
"""


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

# Internal imports

# Local variables


# end_common/__init__.py

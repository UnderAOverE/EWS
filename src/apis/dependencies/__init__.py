#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/dependencies/__init__.py.                                                      #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Package marker for the dependencies layer — per-context runtime containers and      #
#                 FastAPI dependency providers (currently zelle).                                     #
# Dependencies  : Standard library only (sys).                                                        #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


"""
Dependencies layer: per-context runtime containers wired in the host app's lifespan and the
FastAPI dependency providers that reach them (currently ``zelle``).
"""


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

# Internal imports

# Local variables


# end_apis/dependencies/__init__.py

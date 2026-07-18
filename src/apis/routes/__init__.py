#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/routes/__init__.py.                                                            #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Package marker for the routes layer — thin FastAPI routers grouped per bounded      #
#                 context (currently zelle).                                                          #
# Dependencies  : Standard library only (sys).                                                        #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


"""
Routes layer: thin FastAPI routers grouped per bounded context (currently ``zelle``). Handlers
validate, delegate to services, and translate results — no business logic lives here.
"""


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

# Internal imports

# Local variables


# end_apis/routes/__init__.py

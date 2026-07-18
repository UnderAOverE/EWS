#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : src/__init__.py.                                                                    #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Package marker for the src root. The host app (fdn-c-amp-fapis-py) imports          #
#                 every internal module through the src. prefix (from src.apis.routes import ...),    #
#                 so this repo mirrors that convention exactly for drop-in compatibility.             #
# Dependencies  : Standard library only (sys).                                                        #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


"""
The ``src`` package root, mirroring the host app's ``src.apis.*`` import convention.
"""


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

# Internal imports

# Local variables


# end_src/__init__.py

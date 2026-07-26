#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/dependencies/services/__init__.py.                                             #
# Date of birth : 2026-07-26.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Package marker for per-context service dependency providers (currently zelle),      #
#                 mirroring the host app's dependencies/services/{ose,saas}.py convention.            #
# Dependencies  : Standard library only (sys).                                                        #
# Modifications : 2026-07-26 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


"""
Service dependency providers grouped per bounded context (currently ``zelle``): the runtime
container wiring and the FastAPI providers that reach it, matching the host app's
``dependencies/services`` layout.
"""


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

# Internal imports

# Local variables


# end_apis/dependencies/services/__init__.py

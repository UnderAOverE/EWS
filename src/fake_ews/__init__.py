#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : fake_ews/__init__.py.                                                               #
# Date of birth : 2026-07-18.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Package marker for the fake EWS stub — a self-contained FastAPI app faking the      #
#                 /token endpoint and the four ZOMS operations with fault injection, so the           #
#                 broker and facade verify end-to-end before CAT credentials exist.                   #
# Dependencies  : Standard library only (sys).                                                        #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


"""
Fake EWS: a FastAPI stub of the EWS ``/token`` endpoint and the four ZOMS maintenance-event
operations, with in-memory lifecycle state and configurable fault injection.
"""


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

# Internal imports

# Local variables


# end_fake_ews/__init__.py

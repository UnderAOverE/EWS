#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/models/zelle/__init__.py.                                                      #
# Date of birth : 2026-07-16.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Package marker for the zelle model planes (enums, errors, northbound,               #
#                 southbound, records).                                                               #
# Dependencies  : Standard library only (sys).                                                        #
# Modifications : 2026-07-16 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


"""
Zelle model planes: ``enums`` (closed vocabularies), ``errors`` (consumer envelope + facade error
hierarchy), ``northbound`` (consumer wire), ``southbound`` (EWS wire), and ``records``
(internal persistence shapes). Northbound and southbound never share classes or vocabulary.
"""


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

# Internal imports

# Local variables


# end_apis/models/zelle/__init__.py

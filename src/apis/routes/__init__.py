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
# Explanation   : Routes layer package: thin FastAPI routers grouped per bounded context. Re-         #
#                 exports the zelle routers under host-app naming (zelle_events_router,               #
#                 zelle_admin_router) so the host main.py can import and include them alongside       #
#                 the ose/saas routers per its established pattern.                                   #
# Dependencies  : apis.routes.zelle.admin, apis.routes.zelle.events.                                  #
# Modifications : 2026-07-18 Shane Reddy — Initial version.                                           #
#                                                                                                     #
# Contact       : shanevreddy@gmail.com.                                                              #
#                                                                                                     #
# ----------------------------------------------------------------------------------------------------#
#
#


"""
Routes layer: thin FastAPI routers grouped per bounded context (currently ``zelle``). Handlers
validate, delegate to services, and translate results — no business logic lives here. The zelle
routers are re-exported here under host-app naming so the host ``main.py`` can include them the
same way it includes the ose/saas routers; the service itself is built in the lifespan via
``ZelleService.get_service`` and exception handlers via ``add_zelle_exception_handlers(app)``.
"""


# ----------------------------------------------------------------------------------------------------#
# Imports.                                                                                            #
# ----------------------------------------------------------------------------------------------------#

import sys

sys.dont_write_bytecode = True

# External imports

# Internal imports

from src.apis.routes.zelle.admin import admin_router as zelle_admin_router
from src.apis.routes.zelle.events import events_router as zelle_events_router

# Local variables

__all__ = ["zelle_admin_router", "zelle_events_router"]


# end_apis/routes/__init__.py

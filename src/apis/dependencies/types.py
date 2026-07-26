#!/usr/bin/env python


#
#
# ----------------------------------------------------------------------------------------------------#
#                                                                                                     #
# File Name     : apis/dependencies/types.py.                                                         #
# Date of birth : 2026-07-26.                                                                         #
# Version       : 1.0.0.                                                                              #
# Author        : Shane Reddy.                                                                        #
#                                                                                                     #
# Explanation   : Annotated FastAPI dependency aliases for the zelle routes, mirroring the host        #
#                 app's dependencies/types.py convention (e.g. OSEAuditServiceDependency). Route        #
#                 handlers annotate parameters with these aliases instead of inline Depends(...).      #
# Dependencies  : fastapi, apis.dependencies.services.zelle, apis.services.zelle.event_service.        #
# Modifications : 2026-07-26 Shane Reddy — Initial version.                                            #
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

from typing import Annotated

from fastapi import Depends

# Internal imports

from src.apis.dependencies.services.zelle import (
    get_zelle_correlation_id,
    get_zelle_service,
    require_zelle_client_id,
)
from src.apis.services.zelle.event_service import EventService

# Local variables


# ----------------------------------------------------------------------------------------------------#
# Classes or functions.                                                                               #
# ----------------------------------------------------------------------------------------------------#


# The event orchestration service for a request.
ZelleEventServiceDependency = Annotated[EventService, Depends(get_zelle_service)]
# The effective correlation id (consumer-supplied or minted), bound to request.state.
ZelleCorrelationIdDependency = Annotated[str, Depends(get_zelle_correlation_id)]
# The attributed, allowlist-checked caller identity from X-Client-Id.
ZelleClientIdDependency = Annotated[str, Depends(require_zelle_client_id)]


# end_apis/dependencies/types.py

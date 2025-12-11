"""Testing utils."""

from datetime import datetime
from datetime import timedelta
from datetime import UTC
from logging import getLogger
from time import sleep
from typing import TYPE_CHECKING

from pyk8sdev.exceptions import ApplyResourceTimedOutError

if TYPE_CHECKING:
    from pytest_kubernetes.providers import AClusterManager

logger = getLogger(__name__)


def wait_for_created(
    k8s: AClusterManager,
    selector: str,
    kind: str = "pod",
    namespace: str = "default",
    timeout: float = 300,
) -> None:
    """Return when the resource has been created.

    timeout: seconds to wait before raising TimeoutError
    """
    until = datetime.now(tz=UTC) + timedelta(seconds=timeout)
    while datetime.now(tz=UTC) < until:
        try:
            resource = k8s.kubectl(["get", kind, "--namespace", namespace, selector])
        except RuntimeError:
            sleep(1)
            continue
        if (resource["kind"] == "List" and resource["items"]) or resource["kind"].casefold() == kind.casefold():
            return
    raise ApplyResourceTimedOutError(kind, namespace, selector)

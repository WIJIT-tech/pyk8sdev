"""Exceptions for pyk8sdev."""


class PyK8sDevError(Exception):
    """Common exception class for all custom exceptions."""


class CantNameOCIRepoError(ValueError, PyK8sDevError):
    """Can't name an OCI repository."""

    def __init__(self) -> None:
        """Can't name an OCI repository."""
        super(ValueError, self).__init__("Can't name an OCI repository.")


class MustNameNonOCIRepoError(ValueError, PyK8sDevError):
    """Non-OCI repositories require a name."""

    def __init__(self) -> None:
        """Non-OCI repositories require a name."""
        super(ValueError, self).__init__("Non-OCI repositories require a name.")


class BinaryNotFoundError(RuntimeError, PyK8sDevError):
    """Could not find binary in $PATH."""

    def __init__(self, binary: str):
        """Could not find binary in $PATH."""
        super(RuntimeError, self).__init__(f"{binary} not found in PATH.")


class RegistriesNotInConfigError(RuntimeError, PyK8sDevError):
    """kind provider config must include containerdConfigPatches for local registries.

    https://kind.sigs.k8s.io/docs/user/local-registry/
    """

    def __init__(self) -> None:
        """Kind provider config must include containerdConfigPatches for local registries."""
        super(RuntimeError, self).__init__(
            "kind provider config must include containerdConfigPatches for local registries. "
            "See: https://kind.sigs.k8s.io/docs/user/local-registry/"
        )


class UnknownResourceError(TypeError, PyK8sDevError):
    """Unknown resource type."""

    def __init__(self) -> None:
        """Unknown resource type."""
        super(TypeError, self).__init__("Unknown resource type.")


class ProviderNotAvailableError(RuntimeError, PyK8sDevError):
    """Unable to find the provider."""

    def __init__(self, provider: str):
        """Unable to find the provider."""
        super(RuntimeError, self).__init__(f"Provider {provider} not available.")


class ApplyResourceTimedOutError(TimeoutError, PyK8sDevError):
    """Timeout waiting for resource."""

    def __init__(self, kind: str, namespace: str, selector: str):
        """Timeout waiting for resource."""
        super(TimeoutError, self).__init__(f"Timeout waiting for {kind} {namespace}/{selector}.")


class TooOldError(RuntimeError, PyK8sDevError):
    """You must specify a newer api_version for Talos to work.

    See: https://docs.siderolabs.com/talos/v1.11/getting-started/support-matrix
    """

    def __init__(self) -> None:
        """You must specify a newer api_version for Talos to work."""
        super(RuntimeError, self).__init__(
            "You must specify a newer api_version for Talos to work. "
            "See: https://docs.siderolabs.com/talos/v1.11/getting-started/support-matrix"
        )


class MissingKernelModuleError(RuntimeError, PyK8sDevError):
    """br_netfilter module must be loaded for talosctl to create a working cluster."""

    def __init__(self) -> None:
        """br_netfilter module must be loaded for talosctl to create a working cluster."""
        super(RuntimeError, self).__init__(
            "br_netfilter module must be loaded for talosctl to create a working cluster."
        )


class UnsupportedError(RuntimeError, PyK8sDevError):
    """load_image not supported for talos clusters."""

    def __init__(self) -> None:
        """load_image not supported for talos clusters."""
        super(RuntimeError, self).__init__("load_image not supported for talos clusters.")

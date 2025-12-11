# pyk8sdev

> A development tool for creating Kubernetes clusters and syncing code changes to the cluster

This tool extends the functionality of [pytest-kubernetes](https://github.com/Blueshoe/pytest-kubernetes) by adding
support for caching container images, a CLI tool for spinning up a cluster during development, and support for
automatically syncing changes to the cluster.

## Features

- Creates Kubernetes clusters with container caching to speed up development cycles
- Watches for code changes and syncs changes to the cluster
- talosctl provider for pytest-kubernetes
- Provides pytest fixtures
- Standalone CLI tool
- Optional TUI tool

> Only talosctl and kind providers have been tested

## Installation

Install pyk8sdev for use as a pytest fixture or with the simple CLI in your project:

```bash
uv add pyk8sdev
```

To install the full TUI tool:

```bash
uv tool install "pyk8sdev[tui]"
```

## Usage

### Standalone CLI

```bash
pyk8sdev
```

### As pytest plugin

```python
from pyk8sdev import CachedK8sCluster


def test_cluster(cached_k8s_cluster: CachedK8sCluster):
    nodes = cached_k8s_cluster.cluster.kubectl(["get", "nodes"])
    assert len(nodes.get("items", [])) > 0
```

## Configuration

Configuration is managed through `.pyk8sdev.yaml` in your project root:

```yaml
cluster_name: test
provider: kind
api_version: 1.34.0
provider_config: tests/kind.yaml
containers:
  - name: my-app
    tag: latest
    containerfile: Containerfile
    directory: .
resources:
  - source: |-
      https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.1/standard-install.yaml
  - name: cilium
    namespace: kube-system
    version: 1.19.1
    repository_url: oci://quay.io/cilium/charts/cilium
    values_override: |-
      ---
      image:
        pullPolicy: IfNotPresent
      ipam:
        mode: kubernetes
  - source: tests/manifests.yaml
  - name: my-app
    directory: charts/my-app
    values_file: tests/values.yaml
  - command: /usr/local/bin/telepresence helm install
```

All relative paths are relative to the configuration file's directory. Containers are built before resources are
applied. Containers and resources are applied in the order they are listed. A schema can be created to assist in
managing the config using:

```shell
pyk8sdev --schema
```

The schema will have the same path and name as the config you provide, but with the `.schema.json` extension.

## License

This project is licensed under the Apache-2.0 License - see the [LICENSE](./LICENSE) file for details.

## Contributing

PRs are welcome!

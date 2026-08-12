# Thin wrapper over the upstream SearXNG image.
#
# This exists because a fly.toml `image = …` reference alone cannot deliver a
# configuration file: the container would boot with upstream defaults, JSON
# output would stay disabled, and every gateway search would return 403.
# Baking settings.yml in keeps the deployment reproducible and needs no volume.
#
# No secret is baked in. settings.yml carries only the `ultrasecretkey`
# sentinel, which the upstream entrypoint replaces at start-up from
# $SEARXNG_SECRET.

FROM searxng/searxng:latest

# Upstream reads its configuration from this path.
COPY settings.yml /etc/searxng/settings.yml

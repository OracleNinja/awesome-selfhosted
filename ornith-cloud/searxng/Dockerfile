# syntax=docker/dockerfile:1

# Private SearXNG backend for the Ornith gateway.
#
# Uses the current upstream image and overlays a settings.yml that enables the
# JSON search output the gateway requires. The settings file is the only thing
# we change; everything else (engines, granian runtime, bind address) comes
# from upstream defaults, which already listen on [::]:8080 as required by
# Fly's IPv6-only private network.
#
# No secrets are baked into the image: settings.yml carries only a placeholder
# secret key, and the real one is injected at runtime via the SEARXNG_SECRET
# environment variable (Fly secret).

FROM searxng/searxng:latest

# The image reads configuration from /etc/searxng/settings.yml. Overlaying the
# file here means the image's entrypoint will keep it as-is (it only generates
# a settings.yml when the file is absent).
COPY settings.yml /etc/searxng/settings.yml

FROM searxng/searxng:latest

# Copy custom settings that enables search engines + JSON API
COPY settings.yml /etc/searxng/settings.yml

# Railway uses PORT env var
ENV PORT=8080
EXPOSE 8080

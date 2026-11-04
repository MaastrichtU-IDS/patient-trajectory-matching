# Static hosting for the interface mockups. The synthetic dataset is regenerated at build
# time from the committed generator (deterministic for its seed), never copied in.
FROM python:3.12-slim AS data
WORKDIR /src
COPY synthetic/ synthetic/
RUN python synthetic/generate.py --output /src/data/synthetic-paediatric-rare.json

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html README.md /usr/share/nginx/html/
COPY shared/ /usr/share/nginx/html/shared/
COPY patient-first/ /usr/share/nginx/html/patient-first/
COPY trajectory/ /usr/share/nginx/html/trajectory/
COPY --from=data /src/data/ /usr/share/nginx/html/data/
EXPOSE 8080

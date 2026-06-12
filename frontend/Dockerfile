# Production frontend: Angular build served by nginx, /api proxied to the API.
FROM node:24-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npx ng build --configuration production

FROM nginx:1.27-alpine

COPY --from=build /app/dist/equityscope-ui/browser /usr/share/nginx/html
RUN printf 'server {\n\
    listen 8501;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
    location /api/ { proxy_pass http://api:8000; proxy_buffering off; }\n\
    location /healthz { proxy_pass http://api:8000; }\n\
    location / { try_files $uri $uri/ /index.html; }\n\
}\n' > /etc/nginx/conf.d/default.conf && rm -f /etc/nginx/conf.d/default.conf.bak

EXPOSE 8501

CMD ["nginx", "-g", "daemon off;"]

FROM node:20-alpine as frontend-deps

WORKDIR /home/frontend/

COPY ./src/frontend/package.json ./package.json
COPY ./src/frontend/yarn.lock ./yarn.lock
COPY ./src/frontend/apps/main/package.json ./apps/main/package.json
COPY ./src/frontend/packages/eslint-config-main/package.json ./packages/eslint-config-main/package.json

RUN yarn install --frozen-lockfile

COPY .dockerignore ./.dockerignore
COPY ./src/frontend/ .

### ---- Front-end builder image ----
FROM frontend-deps as main

WORKDIR /home/frontend/apps/main

FROM frontend-deps as main-dev

WORKDIR /home/frontend/apps/main

EXPOSE 3000

CMD [ "yarn", "dev"]

# Tilt will rebuild main target so, we dissociate main and main-builder
# to avoid rebuilding the app at every changes.
FROM main as main-builder

WORKDIR /home/frontend/apps/main

RUN yarn build

# ---- Front-end image ----
FROM nginxinc/nginx-unprivileged:1.25 as frontend-production

# Un-privileged user running the application
ARG DOCKER_USER
USER ${DOCKER_USER}

COPY --from=main-builder \
    /home/frontend/apps/main/out \
    /usr/share/nginx/html

COPY ./src/frontend/apps/main/conf/default.conf /etc/nginx/conf.d
COPY ./docker/files/usr/local/bin/entrypoint /usr/local/bin/entrypoint

ENTRYPOINT [ "/usr/local/bin/entrypoint" ]

CMD ["nginx", "-g", "daemon off;"]

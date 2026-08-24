# Stage 1: Build the React application
FROM node:26-alpine AS builder

WORKDIR /app

# Copy package files
COPY package.json package-lock.json ./

# Install dependencies
RUN npm ci

# Copy source code
COPY . .

# Build arguments (injected at build time by Railway env vars)
ARG VITE_API_BASE
ENV VITE_API_BASE=$VITE_API_BASE
ARG VITE_CLERK_PUBLISHABLE_KEY
ENV VITE_CLERK_PUBLISHABLE_KEY=$VITE_CLERK_PUBLISHABLE_KEY
ARG VITE_TELEMETRY_URL
ENV VITE_TELEMETRY_URL=$VITE_TELEMETRY_URL
ARG VITE_TELEMETRY_KEY
ENV VITE_TELEMETRY_KEY=$VITE_TELEMETRY_KEY
ARG VITE_APP_VERSION
ENV VITE_APP_VERSION=$VITE_APP_VERSION

# Build the application
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine

# Copy custom nginx configuration template
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

# Copy built assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Railway provides PORT env variable, default to 80 for local
ENV PORT=80

# Expose the port (documentation only)
EXPOSE ${PORT}

# nginx:alpine image automatically processes templates in /etc/nginx/templates/
# and substitutes environment variables, outputting to /etc/nginx/conf.d/
CMD ["nginx", "-g", "daemon off;"]

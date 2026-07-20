const fs = require('fs');
const path = require('path');

const dotenv = require('dotenv');

/** @type {import('next').NextConfig} */
const frontendRoot = path.join(__dirname, '../..');
const repoRoot = path.join(__dirname, '../../../../');
const reactPath = path.join(frontendRoot, 'node_modules/react');
const reactDomPath = path.join(frontendRoot, 'node_modules/react-dom');

const commonEnvPath = path.join(repoRoot, 'env.d/development/common');
if (fs.existsSync(commonEnvPath)) {
  dotenv.config({ path: commonEnvPath });
}

const frontendTheme = process.env.FRONTEND_THEME || 'default';

const nextConfig = {
  env: {
    FRONTEND_THEME: frontendTheme,
  },
  output: 'export',
  outputFileTracingRoot: frontendRoot,
  trailingSlash: true,
  transpilePackages: [
    '@gouvfr-lasuite/cunningham-react',
    '@gouvfr-lasuite/integration',
    '@gouvfr-lasuite/ui-kit',
    '@tanstack/react-query',
  ],
  images: {
    unoptimized: true,
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'lasuite.numerique.gouv.fr',
        pathname: '/assets/**',
      },
    ],
  },
  compiler: {
    // Enables the styled-components SWC transform
    styledComponents: true,
  },
  sassOptions: {
    includePaths: [path.join(__dirname, 'styles')],
  },
  turbopack: {
    rules: {
      '*.svg': {
        loaders: ['@svgr/webpack'],
        as: '*.js',
      },
    },
  },
  webpack(config, { dev, isServer }) {
    if (dev && !isServer) {
      config.resolve.alias = {
        ...config.resolve.alias,
        react: reactPath,
        'react-dom': reactDomPath,
        'react-dom/client': path.join(reactDomPath, 'client.js'),
        'react/jsx-runtime': path.join(reactPath, 'jsx-runtime.js'),
        'react/jsx-dev-runtime': path.join(reactPath, 'jsx-dev-runtime.js'),
      };
    }
    // Grab the existing rule that handles SVG imports
    const fileLoaderRule = config.module.rules.find((rule) =>
      rule.test?.test?.('.svg'),
    );

    config.module.rules.push(
      // Reapply the existing rule, but only for svg imports ending in ?url
      {
        ...fileLoaderRule,
        test: /\.svg$/i,
        resourceQuery: /url/, // *.svg?url
      },
      // Convert all other *.svg imports to React components
      {
        test: /\.svg$/i,
        issuer: fileLoaderRule.issuer,
        resourceQuery: { not: [...fileLoaderRule.resourceQuery.not, /url/] }, // exclude if *.svg?url
        use: ['@svgr/webpack'],
      },
    );

    // Modify the file loader rule to ignore *.svg, since we have it handled now.
    fileLoaderRule.exclude = /\.svg$/i;

    return config;
  },
};

module.exports = nextConfig;

module.exports = {
  root: true,
  extends: ['main/next', 'plugin:storybook/recommended'],
  parserOptions: {
    tsconfigRootDir: __dirname,
    project: ['./tsconfig.json'],
  },
  settings: {
    next: {
      rootDir: __dirname,
    },
  },
  ignorePatterns: ['node_modules', '.eslintrc.js', 'service-worker.js'],
};

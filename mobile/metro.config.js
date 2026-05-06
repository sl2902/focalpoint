const { getDefaultConfig } = require('expo/metro-config');
const path = require('path');

const config = getDefaultConfig(__dirname);

const existingBlockList = Array.isArray(config.resolver.blockList)
  ? config.resolver.blockList
  : config.resolver.blockList
  ? [config.resolver.blockList]
  : [];

config.resolver.blockList = [
  ...existingBlockList,
  /.*\/\.git\/.*/,
  /.*\.db$/,
  /.*\.db-shm$/,
  /.*\.db-wal$/,
];

config.watchFolders = [
  path.resolve(__dirname),
];

module.exports = config;

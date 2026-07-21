#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SRC_ROOT = path.join(FRONTEND_ROOT, 'src');
const FEATURE_ROOT = path.join(SRC_ROOT, 'features');

const EXPECTED_FEATURES = new Set([
  'automation',
  'cad-processing',
  'dashboard',
  'excel-processing',
  'files',
  'identity',
  'jobs',
  'operations',
  'projects',
  'reviews',
  'workflows',
]);
const LEGACY_SOURCE_DIRECTORIES = ['api', 'components', 'hooks', 'stores', 'types', 'utils'];
const REQUIRED_SHARED_FILES = [
  'shared/api/client.ts',
  'shared/api/error.ts',
  'shared/auth/guards.tsx',
  'shared/auth/index.ts',
  'shared/auth/store.ts',
  'shared/auth/useAuthInit.ts',
  'shared/components/index.ts',
];
const SOURCE_EXTENSIONS = ['.ts', '.tsx'];
const RESOLUTION_SUFFIXES = ['', '.ts', '.tsx', '.css', '/index.ts', '/index.tsx'];

function posix(relativePath) {
  return relativePath.split(path.sep).join('/');
}

function relativeToSource(filePath) {
  return posix(path.relative(SRC_ROOT, filePath));
}

function walk(directory) {
  if (!fs.existsSync(directory)) return [];
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const entryPath = path.join(directory, entry.name);
    return entry.isDirectory() ? walk(entryPath) : [entryPath];
  });
}

function sourceFiles() {
  return walk(SRC_ROOT).filter((filePath) => SOURCE_EXTENSIONS.includes(path.extname(filePath)));
}

function importSpecifiers(source) {
  const specifiers = new Set();
  const staticImport = /\b(?:import|export)\s+(?:type\s+)?(?:[\w*\s{},]+\s+from\s+)?['"]([^'"]+)['"]/g;
  const dynamicImport = /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
  for (const expression of [staticImport, dynamicImport]) {
    for (const match of source.matchAll(expression)) specifiers.add(match[1]);
  }
  return specifiers;
}

function resolveRelativeImport(sourceFile, specifier) {
  if (!specifier.startsWith('.')) return null;
  const base = path.resolve(path.dirname(sourceFile), specifier);
  for (const suffix of RESOLUTION_SUFFIXES) {
    const candidate = path.normalize(`${base}${suffix}`);
    if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
  }
  return null;
}

function featureName(filePath) {
  const relative = path.relative(FEATURE_ROOT, filePath);
  if (relative.startsWith('..') || path.isAbsolute(relative)) return null;
  return relative.split(path.sep)[0] || null;
}

function featureEntry(feature) {
  return path.join(FEATURE_ROOT, feature, 'index.ts');
}

function checkLayout(violations) {
  for (const directory of LEGACY_SOURCE_DIRECTORIES) {
    const legacyPath = path.join(SRC_ROOT, directory);
    if (fs.existsSync(legacyPath)) {
      violations.push(`legacy source directory must be retired: src/${directory}`);
    }
  }

  const actualFeatures = fs.existsSync(FEATURE_ROOT)
    ? fs.readdirSync(FEATURE_ROOT, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort()
    : [];
  const expectedFeatures = [...EXPECTED_FEATURES].sort();
  if (JSON.stringify(actualFeatures) !== JSON.stringify(expectedFeatures)) {
    violations.push(
      `feature directories differ: expected ${expectedFeatures.join(', ')}; found ${actualFeatures.join(', ')}`,
    );
  }

  for (const feature of EXPECTED_FEATURES) {
    const entry = featureEntry(feature);
    if (!fs.existsSync(entry)) violations.push(`missing public feature entry: ${relativeToSource(entry)}`);
  }
  for (const relativePath of REQUIRED_SHARED_FILES) {
    const expected = path.join(SRC_ROOT, relativePath);
    if (!fs.existsSync(expected)) violations.push(`missing shared boundary: src/${relativePath}`);
  }
}

function checkDependency(sourceFile, targetFile, violations) {
  const sourceFeature = featureName(sourceFile);
  const targetFeature = featureName(targetFile);
  if (!targetFeature) return;

  const sourceRelative = relativeToSource(sourceFile);
  const targetRelative = relativeToSource(targetFile);
  const targetEntry = featureEntry(targetFeature);
  const usesPublicEntry = path.normalize(targetFile) === path.normalize(targetEntry);

  if (sourceRelative.startsWith('shared/')) {
    violations.push(`shared must not depend on a feature: ${sourceRelative} -> ${targetRelative}`);
    return;
  }

  if (sourceFeature && sourceFeature !== targetFeature && !usesPublicEntry) {
    violations.push(
      `cross-feature import must use ${targetFeature}/index.ts: ${sourceRelative} -> ${targetRelative}`,
    );
    return;
  }

  const isCompositionRoot = sourceRelative.startsWith('app/')
    || sourceRelative === 'App.tsx'
    || sourceRelative === 'main.tsx';
  if (!sourceFeature && isCompositionRoot && !usesPublicEntry) {
    violations.push(
      `composition root must use ${targetFeature}/index.ts: ${sourceRelative} -> ${targetRelative}`,
    );
  }
}

function main() {
  const violations = [];
  checkLayout(violations);

  const files = sourceFiles();
  for (const sourceFile of files) {
    const source = fs.readFileSync(sourceFile, 'utf8');
    for (const specifier of importSpecifiers(source)) {
      const targetFile = resolveRelativeImport(sourceFile, specifier);
      if (targetFile) checkDependency(sourceFile, targetFile, violations);
    }
  }

  if (violations.length) {
    console.error(`Frontend architecture check failed (${violations.length} violation(s)):`);
    for (const violation of [...new Set(violations)].sort()) console.error(`- ${violation}`);
    process.exitCode = 1;
    return;
  }

  console.log(
    `Frontend architecture check passed: ${files.length} source files, ${EXPECTED_FEATURES.size} feature boundaries.`,
  );
}

main();

/**
 * expo-widgets, with its widget extension built for iOS 17 and later.
 *
 * The « Planning » widget's « Afficher » choice (« Modifier le widget » on the iPhone) uses App
 * Intents, which exist from iOS 17: their Swift code does not compile for iOS 16.4. expo-widgets
 * builds its extension for `ios.deploymentTarget`, so that value is set to 17.0 while the plugin
 * runs, then given back. The app itself keeps its minimum iOS: on an older iPhone it installs and
 * runs, without the widgets.
 */
const plugin = require('expo-widgets/app.plugin');

const withWidgets = plugin.default ?? plugin;

const WIDGETS_IOS = '17.0';

module.exports = function withWidgetsIos17(config, props) {
  const appTarget = config.ios?.deploymentTarget;
  const next = withWidgets({ ...config, ios: { ...config.ios, deploymentTarget: WIDGETS_IOS } }, props);
  const ios = { ...next.ios };
  if (appTarget === undefined) delete ios.deploymentTarget;
  else ios.deploymentTarget = appTarget;
  return { ...next, ios };
};

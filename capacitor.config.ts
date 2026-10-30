import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.globalenterprises.ims',
  appName: 'Global-IMS',
  webDir: 'mobile_shell',

  // Direct WebView connection to the Django ASGI host
  server: {
    // In production, point to the live deployment:
    // url: 'https://global-ims.onrender.com',
    // For local Android emulator testing:
    url: 'https://global-ims.onrender.com',
    cleartext: false,
    androidScheme: 'https',
  },

  plugins: {
    // Industrial dark-slate status bar styling (#020617)
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#020617',
      overlaysWebView: false,
    },

    // Factory floor branded splash screen
    SplashScreen: {
      launchShowDuration: 1500,
      launchAutoHide: true,
      backgroundColor: '#020617',
      androidSplashResourceName: 'splash',
      androidScaleType: 'CENTER_CROP',
      showSpinner: true,
      spinnerColor: '#0891b2',
    },

    // App state & navigation listeners
    App: {
      launchShowDuration: 0,
    },
  },
};

export default config;

# Build Ledger phone app (saves documents as real files on the phone)

A web page in a phone browser cannot write into folders on the phone. This small wrapper app can.
It opens your Build Ledger server inside an app and adds access to phone storage. Documents are then saved as real
files in `Documents/BuildLedger/<Sales invoices | Vendor purchases | Projects | Vendors | Customers>/<invoice or purchase number>/`.

I have NOT built or run this on a real phone. Treat the first build as a test.

## What you need
- Your Build Ledger server reachable at a stable **https** address (for example the add-on's port 8099 behind Tailscale or a reverse proxy).
  Do not use the Home Assistant sidebar (ingress) address: it contains a temporary token.
- Node.js on your computer.
- Android: Android Studio. iPhone: a Mac with Xcode (and an Apple ID).

## Steps
1. Edit `capacitor.config.json` and put your https address in `server.url`.
2. In this folder run:
   ```
   npm install @capacitor/core @capacitor/cli @capacitor/android @capacitor/ios @capacitor/filesystem
   npx cap add android        (and/or: npx cap add ios)
   npx cap sync
   ```
3. Android: add these lines inside `<manifest>` in `android/app/src/main/AndroidManifest.xml`:
   ```
   <uses-permission android:name="android.permission.CAMERA"/>
   <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" android:maxSdkVersion="32"/>
   <uses-permission android:name="android.permission.READ_EXTERNAL_STORAGE" android:maxSdkVersion="32"/>
   ```
   Then `npx cap open android`, and in Android Studio choose **Build, Build Bundle(s)/APK(s), Build APK(s)**.
   Copy the APK to your phone and install it (allow installs from this source when asked).
4. iPhone: `npx cap open ios`, set your signing team in Xcode, and add to `ios/App/App/Info.plist`:
   `UIFileSharingEnabled` = YES and `LSSupportsOpeningDocumentsInPlace` = YES
   so the BuildLedger folder shows in the Files app. Run it on your phone from Xcode.
5. Open the app, sign in, then in **More, Where documents are saved** the phone storage option appears and is selected.

## If saving fails on a newer Android
Recent Android versions restrict writing to the public Documents folder. If documents fail to save, open the app's
`index.html` and change `const NDIR='DOCUMENTS'` to `'EXTERNAL'` (the app's own folder, visible in the Files app
under Android/data/com.buildledger.app) or `'DATA'` (private to the app), then re-copy it to your server and reopen.

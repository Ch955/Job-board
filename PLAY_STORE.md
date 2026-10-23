# Job Radar on Google Play

Status: **not published.** The Android project exists (`mobile/`, Capacitor 8) but no
build has been made: the Android SDK download host was blocked in the environment where
this was prepared, so the AAB must be built on your computer.

## What the app is
The same web app as `docs/`, packaged with Capacitor (`appId: app.jobradar`):
- Email/password sign-in works in the app; **Google sign-in uses the phone's account picker**
  (`@capacitor-firebase/authentication`), because Google blocks sign-in inside web views.
- **Connect Gmail** opens the system browser (`@capacitor/browser`); Google returns to the API,
  which sends you back to the app through `app.jobradar://gmail?...` (intent filter in
  `AndroidManifest.xml`, `return_to: android` in the API).
- `allowBackup` is off so sign-in data isn't copied to cloud backups.

## Build on your computer
Requirements: Node 22, Android Studio (with SDK 36), JDK 21.

1. Firebase console → Project settings → **Add app → Android**, package `app.jobradar`.
   Add your debug and release **SHA-1** fingerprints (needed for Google sign-in).
   Download `google-services.json` → `mobile/android/app/google-services.json` (git-ignored).
2. Make sure `docs/app-config.json` has `api_url`, and the API's `ALLOWED_ORIGINS` includes
   `https://localhost`.
3. ```
   cd mobile
   npm ci
   npm run sync          # copies docs/ into www/ and updates the Android project
   npm run open          # Android Studio: run on a phone to test
   ```
4. Release key (keep it and its passwords safe; losing it blocks updates unless you use
   Play App Signing, which is recommended):
   ```
   keytool -genkeypair -v -keystore jobradar-release.jks -alias jobradar -keyalg RSA -keysize 2048 -validity 10000
   ```
   Create `mobile/android/keystore.properties` (git-ignored):
   ```
   storeFile=/absolute/path/jobradar-release.jks
   storePassword=…
   keyAlias=jobradar
   keyPassword=…
   ```
5. `npm run bundle` → `mobile/android/app/build/outputs/bundle/release/app-release.aab`.
   Raise `versionCode` in `mobile/android/app/build.gradle` for every upload.

## Play Console checklist
- Developer account (one-time US$25 fee: **paid, your decision**). New personal accounts must
  run a closed test with at least 12 testers for 14 days before production access (check the
  current rule in Play Console).
- App content: privacy policy URL `https://<your domain>/privacy.html` (review the draft in
  `docs/privacy.html` first), ads: none, target audience 18+, content rating questionnaire.
- **Data safety form** (answer from what the app actually does):
  - Collected: name, email address, phone number (optional), resume/work history, emails
    (read to detect application updates), app activity (job statuses).
  - Purpose: app functionality. Not sold, not shared with third parties; processed by
    Google Firebase/Cloud and your API host as service providers.
  - Encrypted in transit: yes. Encrypted at rest: Gmail tokens and resumes yes (app-level),
    the rest by Firebase.
  - Users can request deletion: yes, in the app (Settings → Delete my account).
- Gmail: Play doesn't review Gmail scopes, Google OAuth verification does (see
  DEPLOYMENT.md §6). Until verified, only test users can connect Gmail.
- Store listing: short description, full description, 512×512 icon (`docs/icons/`),
  feature graphic 1024×500, at least 2 phone screenshots.

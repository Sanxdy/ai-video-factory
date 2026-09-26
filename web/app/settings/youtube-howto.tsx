"use client";

/** The Google Cloud walkthrough for YouTube uploads.
 *
 *  Shared on purpose: this is the one part of setup that people get wrong (the
 *  OAuth client type and the "Testing" publishing status), and Settings is where
 *  they come back to after it has already gone wrong. Two copies would drift,
 *  and the copy that drifts is always the one being read.
 *
 *  Both pieces live here because the warning and the steps are about the same
 *  mistake — the steps say "publish it", the warning says what leaving it costs.
 */

/** The amber box. Shown wherever the connection is being set up. */
export function YouTubeProdWarning({ style }: { style?: React.CSSProperties }) {
  return (
    <div className="msg msg-warn"
         style={{ display: "block", padding: "10px 12px",
                  borderRadius: "var(--radius-sm)", ...style }}>
      <span>
        <strong>Before you connect: set the OAuth consent screen to “In production”.</strong><br />
        Left on “Testing”, Google expires the refresh token after 7 days — day 1 works, day 8
        uploads start failing with an auth error and it looks like an AVF bug. Publishing to
        production is free and needs no Google verification review; you just click through a
        &ldquo;Google hasn&apos;t verified this app&rdquo; screen once.
      </span>
    </div>
  );
}

/** The collapsed six-step walkthrough, from an empty Cloud Console to the JSON. */
export function ClientSecretHowTo() {
  return (
    <details className="howto">
      <summary>How do I get <code>client_secret.json</code>? — six steps, free, no coding</summary>
      <ol>
        <li>
          <strong>Create a project.</strong> Open{" "}
          <a href="https://console.cloud.google.com/projectcreate" target="_blank" rel="noreferrer">console.cloud.google.com/projectcreate</a>.
          Give it any name (<code>avf</code> is fine), leave &ldquo;Location&rdquo; as{" "}
          <em>No organisation</em>, and click <strong>Create</strong>. Wait for the
          notification that says it is ready, then make sure this project is the one
          selected in the <strong>project picker at the very top of the page</strong> — every
          step below applies to whichever project is selected there, and picking the wrong
          one is the most common way to end up with a file AVF cannot use.
        </li>
        <li>
          <strong>Turn on the YouTube API.</strong> Open{" "}
          <a href="https://console.cloud.google.com/apis/library/youtube.googleapis.com" target="_blank" rel="noreferrer">YouTube Data API v3</a>.
          Check the picker still shows your project, then click the blue{" "}
          <strong>Enable</strong> button. If you instead see <strong>Manage</strong>, it is
          already enabled — continue.
        </li>
        <li>
          <strong>Fill in the consent screen.</strong> Open{" "}
          <a href="https://console.cloud.google.com/apis/credentials/consent" target="_blank" rel="noreferrer">the consent screen</a>.
          In newer consoles this page is titled <strong>Google Auth Platform</strong> and is
          split into tabs on the left; in older ones it is one long form. Either way:
          <ul>
            <li>User type: <strong>External</strong> (this means &ldquo;any Google
              account&rdquo;, not &ldquo;outside my company&rdquo;).</li>
            <li>App name: anything — <code>AVF</code>. You are the only person who will ever
              see it.</li>
            <li>User support email and developer contact email: your own address, twice.</li>
            <li>Audience / Publishing status: leave <strong>Testing</strong> for now — the
              next step fixes it. If it asks for <em>test users</em>, add your own Gmail
              address.</li>
            <li>Scopes: click <strong>Add or remove scopes</strong> and paste{" "}
              <code>https://www.googleapis.com/auth/youtube.upload</code> into the filter
              box, tick it, then <strong>Update</strong>.</li>
          </ul>
        </li>
        <li className="trap">
          ⚠️ <strong>Publish the app — do not leave it in &ldquo;Testing&rdquo;.</strong> On
          that same consent screen, find <strong>Publish app</strong> (in the new console it
          is under the <strong>Audience</strong> tab) and confirm. The status must read{" "}
          <strong>In production</strong>. This is the single step everyone misses; see the
          amber box above for what it costs. Publishing is free, needs no Google review, and
          the only visible effect is that you click through a{" "}
          <em>&ldquo;Google hasn&apos;t verified this app&rdquo;</em> screen once — choose{" "}
          <strong>Advanced → Go to AVF (unsafe)</strong>. That wording is Google&apos;s
          default for any app that has not paid for verification; it is not a warning about
          AVF specifically.
        </li>
        <li>
          <strong>Create the credential.</strong> Open{" "}
          <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noreferrer">Credentials</a>
          {" "}→ <strong>+ Create credentials</strong> → <strong>OAuth client ID</strong>.
          Application type must be <strong>Desktop app</strong>. Name it anything and click{" "}
          <strong>Create</strong>.
          <br /><span style={{ color: "var(--color-warning-text)" }}>
            Choose <strong>Desktop app</strong>, not &ldquo;Web application&rdquo;.
          </span>{" "}
          The two produce different JSON files: a Web-application file has a{" "}
          <code>&quot;web&quot;</code> section, a Desktop-app file has an{" "}
          <code>&quot;installed&quot;</code> section, and AVF only accepts the latter.
          Picking the wrong one is the other way to get a file that is rejected here.
        </li>
        <li>
          <strong>Download it.</strong> A dialog appears with your client ID and secret —
          click <strong>Download JSON</strong>. Your browser saves a file named something
          like <code>client_secret_1234-abc.apps.googleusercontent.com.json</code>. Leave the
          name exactly as it is — AVF reads the file&apos;s contents and never looks at the
          filename.
        </li>
      </ol>
    </details>
  );
}

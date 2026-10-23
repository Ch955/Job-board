// Test helper: drive docs/sync.js from Python tests through stdin/stdout.
const JRSync = require("../../docs/sync.js");
let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", async () => {
  const req = JSON.parse(input);
  if (req.op === "seal") {
    const key = await JRSync.deriveKey(req.passphrase, req.salt, req.iter);
    console.log(JSON.stringify(await JRSync.seal(req.state, key, req.salt, req.iter)));
  } else if (req.op === "open") {
    const key = await JRSync.deriveKey(req.passphrase, req.envelope.salt, req.envelope.iter);
    console.log(JSON.stringify(await JRSync.open(req.envelope, key)));
  } else if (req.op === "merge") {
    console.log(JSON.stringify(JRSync.merge(req.a, req.b)));
  }
});

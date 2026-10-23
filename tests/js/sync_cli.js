// Test helper: run docs/sync.js merge from Python tests through stdin/stdout.
const JRSync = require("../../docs/sync.js");
let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
  const req = JSON.parse(input);
  console.log(JSON.stringify(JRSync.merge(req.a, req.b)));
});

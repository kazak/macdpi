import Foundation
import JavaScriptCore

let context = JSContext()!
let source = try String(contentsOfFile: CommandLine.arguments[1], encoding: .utf8)
context.evaluateScript(source)
if let error = context.exception { fatalError("PAC syntax error: \(error)") }
let find = context.objectForKeyedSubscript("FindProxyForURL")!
let cases: [(String, Bool)] = [
    ("www.youtube.com", true), ("r1.googlevideo.com", true), ("gateway.discord.gg", true),
    ("cdn.discordapp.com", true), ("soundcloud.com", true), ("www.twitch.tv", true),
    ("discord-attachments-uploads-prd.storage.googleapis.com", true),
    ("instagram.com", false), ("cdninstagram.com", false), ("fbcdn.net", false),
    ("ig.me", false), ("notyoutube.com", false), ("youtube.com.example.test", false),
    ("discord.com.evil.test", false), ("storage.googleapis.com", false), ("example.com", false)
]
for (host, shouldProxy) in cases {
    let route = find.call(withArguments: ["https://" + host + "/", host])!.toString()!
    precondition(route.hasPrefix("SOCKS5") == shouldProxy, "Unexpected route for \(host): \(route)")
}
print("PAC routing and domain boundaries: OK")

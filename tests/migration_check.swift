import Foundation

@main
struct MigrationCheck {
    static func main() {
        let script = "/Users/example/Library/Application Support/LocalYouTubeBypass/agent.py"
        precondition(ManagedWorker.matches(processInfo: "501 /usr/bin/python3 " + script, uid: 501, scriptPath: script))
        precondition(!ManagedWorker.matches(processInfo: "502 /usr/bin/python3 " + script, uid: 501, scriptPath: script))
        precondition(!ManagedWorker.matches(processInfo: "501 /usr/bin/python3 /tmp/another-service.py", uid: 501, scriptPath: script))
        precondition(!ManagedWorker.matches(processInfo: "501 /usr/bin/python3 " + script + ".backup", uid: 501, scriptPath: script))
        precondition(!ManagedWorker.matches(processInfo: "501 /bin/sh -c " + script, uid: 501, scriptPath: script))
        print("Migration refuses foreign processes: OK")
    }
}

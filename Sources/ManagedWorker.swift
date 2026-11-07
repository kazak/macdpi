import Foundation

enum ManagedWorker {
    /// A listener may be terminated during migration only when both the Unix
    /// owner and the exact Python script argument match this application's worker.
    static func matches(processInfo: String, uid: Int, scriptPath: String) -> Bool {
        let fields = processInfo.trimmingCharacters(in: .whitespacesAndNewlines)
            .split(maxSplits: 1, whereSeparator: { $0.isWhitespace })
        guard fields.count == 2, Int(fields[0]) == uid else { return false }
        let command = String(fields[1])
        let executable = command.split(maxSplits: 1, whereSeparator: { $0.isWhitespace }).first.map(String.init) ?? ""
        guard URL(fileURLWithPath: executable).lastPathComponent.lowercased().hasPrefix("python") else { return false }
        let pattern = "(?:^|\\s)" + NSRegularExpression.escapedPattern(for: scriptPath) + "(?:\\s|$)"
        return command.range(of: pattern, options: .regularExpression) != nil
    }
}

// Polls the modifier state: an event tap would need Accessibility or Input Monitoring.

import AppKit
import CoreGraphics

struct Options {
    var herdr = ""
    var plugin = "alaibe.key-hints"
    var apps: Set<String> = ["com.mitchellh.ghostty"]
    var delay = 0.45
    var log = FileManager.default.homeDirectoryForCurrentUser.path + "/Library/Logs/herdr-key-hints.log"
}

func usage() -> Never {
    let text = "usage: key-hints-watcher --herdr PATH [--plugin ID] [--apps BUNDLE,...] [--delay SECONDS] [--log PATH]"
    FileHandle.standardError.write(Data((text + "\n").utf8))
    exit(2)
}

func parse(_ arguments: [String]) -> Options {
    var options = Options()
    var rest = arguments.dropFirst().makeIterator()
    while let flag = rest.next() {
        guard let value = rest.next() else { usage() }
        switch flag {
        case "--herdr": options.herdr = value
        case "--plugin": options.plugin = value
        case "--apps": options.apps = Set(value.split(separator: ",").map(String.init))
        case "--delay": options.delay = Double(value) ?? options.delay
        case "--log": options.log = value
        default: usage()
        }
    }
    if options.herdr.isEmpty { usage() }
    return options
}

let options = parse(CommandLine.arguments)
let pollInterval = 0.05
let actions = ["cmd": "hold-cmd", "cmd+shift": "hold-cmd-shift", "ctrl": "hold-ctrl"]

func log(_ message: String) {
    let line = "\(ISO8601DateFormatter().string(from: Date())) \(message)\n"
    let data = Data(line.utf8)
    if let handle = FileHandle(forWritingAtPath: options.log) {
        handle.seekToEndOfFile()
        handle.write(data)
        handle.closeFile()
    } else {
        FileManager.default.createFile(atPath: options.log, contents: data)
    }
}

// Named as key_hints.py's HOLDS. ⌥ is never a hold: it types accented letters.
func held(_ flags: CGEventFlags) -> String? {
    let cmd = flags.contains(.maskCommand)
    let ctrl = flags.contains(.maskControl)
    let shift = flags.contains(.maskShift)
    if flags.contains(.maskAlternate) { return nil }
    if cmd && !ctrl { return shift ? "cmd+shift" : "cmd" }
    if ctrl && !cmd && !shift { return "ctrl" }
    return nil
}

func invoke(_ hold: String) {
    guard let action = actions[hold] else { return }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: options.herdr)
    process.arguments = ["plugin", "action", "invoke", action, "--plugin", options.plugin]
    let errors = Pipe()
    process.standardOutput = FileHandle.nullDevice
    process.standardError = errors
    process.terminationHandler = { finished in
        if finished.terminationStatus == 0 {
            log("asked Herdr for the \(hold) card")
        } else {
            let detail = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            log("herdr refused the \(hold) card (\(finished.terminationStatus)): \(detail)")
        }
    }
    do {
        try process.run()
    } catch {
        log("could not run \(options.herdr): \(error)")
    }
}

final class Watcher: NSObject {
    var poll: Timer?
    var current: String?
    var heldSince: Date?

    func start() {
        let size = (try? FileManager.default.attributesOfItem(atPath: options.log)[.size] as? Int) ?? 0
        if size > 1_000_000 { try? Data().write(to: URL(fileURLWithPath: options.log)) }
        NSWorkspace.shared.notificationCenter.addObserver(
            self, selector: #selector(activated(_:)),
            name: NSWorkspace.didActivateApplicationNotification, object: nil
        )
        log("started, pid \(ProcessInfo.processInfo.processIdentifier), watching \(options.apps.sorted())")
        if let front = NSWorkspace.shared.frontmostApplication?.bundleIdentifier, options.apps.contains(front) {
            startPolling(front)
        }
    }

    @objc func activated(_ note: Notification) {
        let app = note.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
        if let id = app?.bundleIdentifier, options.apps.contains(id) { startPolling(id) } else { stopPolling() }
    }

    func startPolling(_ app: String) {
        guard poll == nil else { return }
        poll = Timer.scheduledTimer(withTimeInterval: pollInterval, repeats: true) { [weak self] _ in self?.tick() }
    }

    func stopPolling() {
        guard poll != nil else { return }
        poll?.invalidate()
        poll = nil
        current = nil
        heldSince = nil
    }

    func tick() {
        let flags = CGEventSource.flagsState(.combinedSessionState)
        let now = held(flags)
        if now != current {
            current = now
            heldSince = now == nil ? nil : Date()
        }
        guard let now, let since = heldSince else { return }
        let heldFor = Date().timeIntervalSince(since)
        guard heldFor >= options.delay else { return }
        // Once per hold: the card closes itself on release.
        heldSince = nil
        let sinceKey = CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: .keyDown)
        // A key went down during the hold: that was ⌘C or ⌃C, not a question.
        if sinceKey < heldFor {
            log("key pressed during the hold (\(String(format: "%.2f", sinceKey))s ago), not showing")
            return
        }
        invoke(now)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let watcher = Watcher()
watcher.start()
app.run()

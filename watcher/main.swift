// Hold ⌘, ⌘⇧ or ⌃ in a terminal and Herdr shows the keys under that modifier: the key-hints plugin's hold card.
//
// A terminal program never sees a modifier pressed on its own, and Herdr plugins can't watch keys, so the hold is
// noticed out here, by a background app that polls the modifier state while one of the listed terminals is frontmost.
// Polling rather than an event tap or a global key monitor, because those need Accessibility or Input Monitoring;
// CGEventSource's modifier flags and its time since the last key press need neither. On a hold it invokes the plugin's
// hold-cmd, hold-cmd-shift or hold-ctrl action, which opens the card, and the card closes itself when the modifier is
// let go.
//
//   key-hints-watcher --herdr PATH [--plugin ID] [--apps BUNDLE,BUNDLE] [--delay SECONDS] [--log PATH]
//
// watcher/setup.sh builds it into the plugin's state directory and runs it as a LaunchAgent.

import AppKit
import CoreGraphics

struct Options {
    var herdr = ""
    var plugin = "alaibe.key-hints"
    var apps: Set<String> = ["com.mitchellh.ghostty"]
    // Long enough that ⌘C or ⌃C never flashes the card, short enough to feel like it answers the hold.
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
// The plugin action that opens each card, by the modifier set held.
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

// Named as key_hints.py's HOLDS expects them. ⌥ in the mix is never a hold: it is how accented letters are typed.
func held(_ flags: CGEventFlags) -> String? {
    let cmd = flags.contains(.maskCommand)
    let ctrl = flags.contains(.maskControl)
    let shift = flags.contains(.maskShift)
    if flags.contains(.maskAlternate) { return nil }
    if cmd && !ctrl { return shift ? "cmd+shift" : "cmd" }
    if ctrl && !cmd && !shift { return "ctrl" }
    return nil
}

// Asks Herdr to open the card for `hold`, without waiting for it, so polling carries on meanwhile.
func invoke(_ hold: String) {
    guard let action = actions[hold] else { return }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: options.herdr)
    process.arguments = ["plugin", "action", "invoke", action, "--plugin", options.plugin]
    let errors = Pipe()
    process.standardOutput = FileHandle.nullDevice
    process.standardError = errors
    process.terminationHandler = { finished in
        // Success only means the action ran: it still opens nothing when the hold ended, or a key came, while it
        // started. `herdr plugin log --plugin alaibe.key-hints` says which.
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
        // Only holds and what came of them are logged, a few lines a day; past a megabyte it starts over anyway.
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

    // Polls only while a listed terminal is frontmost, so it costs nothing otherwise.
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
        // Once per hold: the card closes itself on release, and the next hold starts the clock again.
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

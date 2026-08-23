// One window of the listening assistant: the rolling transcript, or the answer.
//
// This exists in Swift for one reason. A window that must never appear in
// material I am sharing has to be excluded by the window server itself, and the
// only thing that does that is NSWindow.sharingType = .none: the window is then
// invisible to ScreenCaptureKit, to CGWindowListCreateImage, to Zoom, Meet and
// Teams, and to this assistant's own screenshot helper. A Hammerspoon canvas
// cannot set it, so the menu bar in Hammerspoon starts this instead. If the
// window server refuses the setting, this refuses to show the window at all: a
// window that could be captured is worse than no window.
//
// It needs no permission of any kind. It reads the daemon's own control socket,
// shows what it is told, and exits when the daemon goes away, so a window can
// never outlive the session it belongs to.
//
//   rvw_view --window transcript [--seconds 300]
//   rvw_view --window answer

import AppKit
import Darwin

let window_kinds = ["transcript", "answer"]
let refresh_seconds: [String: Double] = ["transcript": 2.0, "answer": 0.8]
let unreachable_polls_before_exit = 8
let reply_read_size = 65536

func log_ok(_ message: String) { print("OK   \(message)") }
func log_fail(_ message: String) { FileHandle.standardError.write(("FAIL " + message + "\n").data(using: .utf8)!) }

func die(_ message: String) -> Never {
    log_fail(message)
    exit(1)
}

// MARK: - talking to the assistant

enum ControlError: Error {
    case unreachable(String)
}

/// One command per connection, exactly as bin/rvwctl sends it.
final class ControlClient {
    private let socket_path: String

    init(socket_path: String) {
        self.socket_path = socket_path
    }

    func send(_ command: String) throws -> String {
        let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        if descriptor < 0 {
            throw ControlError.unreachable("no socket: \(String(cString: strerror(errno)))")
        }
        defer { close(descriptor) }
        try connect(descriptor)
        try write(command, to: descriptor)
        return read_whole_reply(from: descriptor)
    }

    private func connect(_ descriptor: Int32) throws {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let path_bytes = Array(socket_path.utf8CString)
        if path_bytes.count > MemoryLayout.size(ofValue: address.sun_path) {
            throw ControlError.unreachable("socket path is too long: \(socket_path)")
        }
        withUnsafeMutableBytes(of: &address.sun_path) { destination in
            path_bytes.withUnsafeBytes { source in
                destination.copyMemory(from: source)
            }
        }
        let connected = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { generic in
                Darwin.connect(descriptor, generic, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        if connected != 0 {
            throw ControlError.unreachable("the assistant is not listening on \(socket_path)")
        }
    }

    private func write(_ command: String, to descriptor: Int32) throws {
        let bytes = Array(command.utf8)
        let written = bytes.withUnsafeBytes { buffer in
            Darwin.write(descriptor, buffer.baseAddress, buffer.count)
        }
        if written != bytes.count {
            throw ControlError.unreachable("could not send \(command)")
        }
    }

    /// Read until the assistant closes the connection: a transcript is not one read.
    private func read_whole_reply(from descriptor: Int32) -> String {
        var received = Data()
        var block = [UInt8](repeating: 0, count: reply_read_size)
        while true {
            let count = Darwin.read(descriptor, &block, reply_read_size)
            if count <= 0 {
                return String(decoding: received, as: UTF8.self)
            }
            received.append(contentsOf: block[0..<count])
        }
    }
}

/// The body of an "OK heading:\nbody" reply, or the whole reply if it failed.
func displayable_text(of reply: String) -> String {
    let trimmed = reply.trimmingCharacters(in: .whitespacesAndNewlines)
    guard trimmed.hasPrefix("OK ") else { return trimmed }
    let without_prefix = String(trimmed.dropFirst(3))
    guard let newline = without_prefix.firstIndex(of: "\n") else { return without_prefix }
    return String(without_prefix[without_prefix.index(after: newline)...])
}

// MARK: - the window

/// A plain scrolling text window that the window server will not let anyone capture.
final class PrivateTextWindow {
    private let window: NSWindow
    private let text_view: NSTextView

    init(title: String, autosave_name: String) {
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 620, height: 360),
                          styleMask: [.titled, .closable, .resizable, .miniaturizable],
                          backing: .buffered, defer: false)
        window.title = title
        window.level = .floating
        window.isReleasedWhenClosed = false
        window.setFrameAutosaveName(autosave_name)
        text_view = PrivateTextWindow.make_text_view()
        window.contentView = PrivateTextWindow.make_scroll_view(around: text_view)
        exclude_from_screen_capture()
    }

    /// The whole reason this program is written in Swift; see the file comment.
    private func exclude_from_screen_capture() {
        window.sharingType = .none
        if window.sharingType != .none {
            die("the window server would not exclude this window from screen capture")
        }
        log_ok("\(window.title) is excluded from screen capture (sharingType none)")
    }

    private static func make_text_view() -> NSTextView {
        let view = NSTextView()
        view.isEditable = false
        view.isRichText = false
        view.font = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        view.textContainerInset = NSSize(width: 8, height: 8)
        view.autoresizingMask = [.width]
        return view
    }

    private static func make_scroll_view(around view: NSTextView) -> NSScrollView {
        let scroll_view = NSScrollView()
        scroll_view.hasVerticalScroller = true
        scroll_view.autohidesScrollers = true
        scroll_view.documentView = view
        return scroll_view
    }

    func show() {
        window.orderFrontRegardless()
    }

    /// Replace the text, keeping the newest speech in view.
    func display(_ text: String) {
        let was_at_bottom = is_scrolled_to_the_bottom()
        text_view.string = text
        if was_at_bottom {
            text_view.scrollToEndOfDocument(nil)
        }
    }

    private func is_scrolled_to_the_bottom() -> Bool {
        guard let clip_view = text_view.enclosingScrollView?.contentView else { return true }
        let remaining = text_view.bounds.height - clip_view.bounds.maxY
        return remaining < 40
    }

    var is_closed: Bool {
        return !window.isVisible
    }
}

// MARK: - the application

final class ViewerApplication: NSObject, NSApplicationDelegate {
    private let window_kind: String
    private let command: String
    private let client: ControlClient
    private var window: PrivateTextWindow?
    private var timer: Timer?
    private var consecutive_failures = 0

    init(window_kind: String, command: String, client: ControlClient) {
        self.window_kind = window_kind
        self.command = command
        self.client = client
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        window = PrivateTextWindow(title: "rvw \(window_kind)", autosave_name: "rvw_\(window_kind)")
        window?.show()
        refresh()
        let period = refresh_seconds[window_kind] ?? 2.0
        timer = Timer.scheduledTimer(withTimeInterval: period, repeats: true) { _ in self.refresh() }
    }

    /// One poll: show what the assistant says, or give up on an assistant that has gone.
    private func refresh() {
        if window?.is_closed ?? true {
            return NSApp.terminate(nil)
        }
        do {
            let reply = try client.send(command)
            consecutive_failures = 0
            window?.display(displayable_text(of: reply))
        } catch {
            note_that_the_assistant_did_not_answer(error)
        }
    }

    /// A window must not outlive the session it is showing, so a daemon that stays
    /// away takes its windows with it rather than leaving a stale transcript on screen.
    private func note_that_the_assistant_did_not_answer(_ error: Error) {
        consecutive_failures += 1
        window?.display("the assistant is not running")
        if consecutive_failures >= unreachable_polls_before_exit {
            log_ok("the assistant has been gone for \(consecutive_failures) polls; closing \(window_kind)")
            NSApp.terminate(nil)
        }
    }
}

// MARK: - the command line

struct Options {
    var window_kind = "transcript"
    var window_seconds: String?
    var socket_path = default_socket_path()

    var command: String {
        if window_kind == "answer" { return "ANSWER" }
        guard let seconds = window_seconds else { return "TRANSCRIPT" }
        return "TRANSCRIPT \(seconds)"
    }
}

/// var/run/rvw.sock beside this binary's repository, or wherever RVW_CONTROL_SOCKET says.
func default_socket_path() -> String {
    if let from_environment = ProcessInfo.processInfo.environment["RVW_CONTROL_SOCKET"] {
        return from_environment
    }
    let binary = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
    let repository = binary.deletingLastPathComponent().deletingLastPathComponent()
    return repository.appendingPathComponent("var/run/rvw.sock").path
}

func parse_options(_ arguments: [String]) -> Options {
    var options = Options()
    var index = 0
    while index < arguments.count {
        switch arguments[index] {
        case "--seconds":
            options.window_seconds = value(after: index, in: arguments, for: "--seconds")
            index += 2
        case "--socket":
            options.socket_path = value(after: index, in: arguments, for: "--socket")
            index += 2
        case "--window":
            options.window_kind = value(after: index, in: arguments, for: "--window")
            index += 2
        default:
            die("unknown argument \(arguments[index]); usage: rvw_view --window transcript|answer")
        }
    }
    if !window_kinds.contains(options.window_kind) {
        die("unknown window \(options.window_kind); known: \(window_kinds.joined(separator: ", "))")
    }
    return options
}

func value(after index: Int, in arguments: [String], for name: String) -> String {
    guard index + 1 < arguments.count else { die("\(name) needs a value") }
    return arguments[index + 1]
}

@main
struct RvwView {
    static func main() {
        let options = parse_options(Array(CommandLine.arguments.dropFirst()))
        let application = NSApplication.shared
        application.setActivationPolicy(.accessory)     // no dock icon, no menu bar of its own
        let delegate = ViewerApplication(window_kind: options.window_kind,
                                        command: options.command,
                                        client: ControlClient(socket_path: options.socket_path))
        application.delegate = delegate
        log_ok("showing the \(options.window_kind) window, reading \(options.socket_path)")
        application.run()
    }
}

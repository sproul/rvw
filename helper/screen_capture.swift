// Capture one screenshot with ScreenCaptureKit and write it to a PNG file.
//
//   screen_capture --output /path/shot.png --target frontmost
//   screen_capture --output /path/shot.png --target display
//
// "frontmost" captures only the frontmost application's largest on screen
// window, which is what a conversation is usually about, and which keeps the
// rest of the desktop out of the archive. "display" captures the whole main
// display and has to be asked for explicitly: capturing everything must never
// happen by accident.
//
// Nothing is displayed: no window, no overlay, no sound, so nothing of this
// appears in a shared screen. The metadata of the capture is printed to stdout
// as one JSON object; diagnostics go to stderr prefixed with "OK " or "FAIL ".
// No interpretation, no network request, no model.

import AppKit
import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

let minimum_window_side_points = 80.0

func log_ok(_ message: String) {
    FileHandle.standardError.write(("OK   " + message + "\n").data(using: .utf8)!)
}

func log_fail(_ message: String) {
    FileHandle.standardError.write(("FAIL " + message + "\n").data(using: .utf8)!)
}

func die(_ message: String) -> Never {
    log_fail(message)
    exit(1)
}

// MARK: - command line

struct CaptureRequest {
    let output_path: String
    let target: String
}

func parse_arguments() -> CaptureRequest {
    var output_path = ""
    var target = "frontmost"
    var arguments = Array(CommandLine.arguments.dropFirst())
    while let flag = arguments.first {
        arguments.removeFirst()
        guard let value = arguments.first else { die(usage_message) }
        arguments.removeFirst()
        switch flag {
        case "--output": output_path = value
        case "--target": target = value
        default: die(usage_message)
        }
    }
    guard !output_path.isEmpty else { die(usage_message) }
    guard target == "frontmost" || target == "display" else {
        die("unknown target \(target); expected frontmost or display")
    }
    return CaptureRequest(output_path: output_path, target: target)
}

let usage_message = "usage: screen_capture --output <path.png> [--target frontmost|display]"

// MARK: - png output

func write_png(_ image: CGImage, to path: String) {
    let url = URL(fileURLWithPath: path)
    guard let destination = CGImageDestinationCreateWithURL(url as CFURL,
                                                            UTType.png.identifier as CFString,
                                                            1, nil) else {
        die("cannot write a PNG to \(path)")
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else { die("cannot finalize the PNG at \(path)") }
}

// MARK: - what to capture

/// Everything ScreenCaptureKit is willing to show us; this is where a missing
/// screen recording permission surfaces.
func shareable_content() async -> SCShareableContent {
    do {
        return try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
    } catch {
        die("cannot list the shareable screen content (\(error.localizedDescription)); the "
            + "application running this helper needs Screen Recording permission in System "
            + "Settings, Privacy and Security")
    }
}

func window_area(_ window: SCWindow) -> Double {
    return window.frame.width * window.frame.height
}

/// The frontmost application's largest window, ignoring palettes and menu extras.
func frontmost_window(in content: SCShareableContent) -> SCWindow? {
    guard let process_id = NSWorkspace.shared.frontmostApplication?.processIdentifier else {
        return nil
    }
    let candidates = content.windows.filter { window in
        window.owningApplication?.processID == process_id
            && window.frame.width >= minimum_window_side_points
            && window.frame.height >= minimum_window_side_points
    }
    return candidates.max(by: { window_area($0) < window_area($1) })
}

func main_display(in content: SCShareableContent) -> SCDisplay {
    let main_display_id = CGMainDisplayID()
    guard let display = content.displays.first(where: { $0.displayID == main_display_id })
            ?? content.displays.first else {
        die("ScreenCaptureKit reported no displays")
    }
    return display
}

// MARK: - capture

/// The filter knows its own content rectangle and its display's point to pixel
/// scale, so the native pixel size needs no AppKit: a command line tool has no
/// window server connection and asking NSScreen for a scale factor aborts the
/// process with CGS_REQUIRE_INIT.
func pixel_size(of filter: SCContentFilter) -> (width: Int, height: Int) {
    let scale = CGFloat(filter.pointPixelScale)
    return (max(1, Int(filter.contentRect.width * scale)),
            max(1, Int(filter.contentRect.height * scale)))
}

func capture_image(filter: SCContentFilter) async -> CGImage {
    let size = pixel_size(of: filter)
    let configuration = SCStreamConfiguration()
    configuration.width = size.width
    configuration.height = size.height
    configuration.showsCursor = true
    do {
        return try await SCScreenshotManager.captureImage(contentFilter: filter,
                                                         configuration: configuration)
    } catch {
        die("the screen capture itself failed (\(error.localizedDescription))")
    }
}

struct Capture {
    let image: CGImage
    let metadata: [String: Any]
}

func capture_frontmost_window(_ content: SCShareableContent) async -> Capture {
    guard let window = frontmost_window(in: content) else {
        die("no window of the frontmost application could be captured; ask for the whole "
            + "display explicitly with --target display or RVW_SCREENSHOT_TARGET=display")
    }
    let image = await capture_image(filter: SCContentFilter(desktopIndependentWindow: window))
    log_ok("captured the frontmost window of "
           + (window.owningApplication?.applicationName ?? "an unknown application"))
    return Capture(image: image, metadata: window_metadata(window, image))
}

func capture_main_display(_ content: SCShareableContent) async -> Capture {
    let display = main_display(in: content)
    let image = await capture_image(filter: SCContentFilter(display: display,
                                                            excludingWindows: []))
    log_ok("captured the whole main display, as asked")
    return Capture(image: image, metadata: display_metadata(display, image))
}

// MARK: - metadata

func window_metadata(_ window: SCWindow, _ image: CGImage) -> [String: Any] {
    return ["target": "window",
            "application": window.owningApplication?.applicationName ?? "",
            "bundle_id": window.owningApplication?.bundleIdentifier ?? "",
            "window_title": window.title ?? "",
            "window_id": Int(window.windowID),
            "width": image.width,
            "height": image.height]
}

func display_metadata(_ display: SCDisplay, _ image: CGImage) -> [String: Any] {
    return ["target": "display",
            "application": NSWorkspace.shared.frontmostApplication?.localizedName ?? "",
            "window_title": "",
            "display_id": Int(display.displayID),
            "width": image.width,
            "height": image.height]
}

func print_metadata(_ metadata: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: metadata, options: [.sortedKeys]),
          let text = String(data: data, encoding: .utf8) else {
        die("cannot serialize the capture metadata")
    }
    print(text)
}

// MARK: - entry point

@main
struct ScreenCaptureTool {
    static func main() async {
        let request = parse_arguments()
        let content = await shareable_content()
        let capture = request.target == "frontmost"
            ? await capture_frontmost_window(content)
            : await capture_main_display(content)
        write_png(capture.image, to: request.output_path)
        print_metadata(capture.metadata)
    }
}

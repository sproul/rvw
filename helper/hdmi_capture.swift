// Capture one frame from an HDMI capture card and write it to a PNG file.
//
//   hdmi_capture --output /path/shot.png --device "Elgato 4K X"
//   hdmi_capture --list-devices
//   hdmi_capture --check-permission
//
// In the two-Mac arrangement the work machine's HDMI output goes into a capture
// card such as the Elgato 4K X, and the card reaches this Mac over USB as an
// ordinary UVC video device. A frame from it is therefore the other Mac's
// screen, and it is archived exactly like a screenshot of this one.
//
// macOS treats every video input as a camera, so reading the card needs the
// Camera permission of the responsible application, which is bin/rvw.app.
// --check-permission asks for it and nothing else, so that
// util/init_permissions.sh can raise the prompt without needing a signal on the
// card. A capture never asks: the daemon gives it seconds, not the minute a
// person may take to answer a prompt, so an undecided permission is reported
// with the command that settles it. --list-devices needs no permission at all.
//
// The metadata of the capture is printed to stdout as one JSON object;
// diagnostics go to stderr prefixed with "OK " or "FAIL ".

import AVFoundation
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// The card's first frames after the stream starts can be black or torn while
/// it locks onto the signal, so this many are thrown away before keeping one.
let frames_to_settle = 10
let frame_timeout_seconds = 10.0
/// A frame whose brightest and darkest sampled pixels differ by less than this
/// (out of 765, the sum of three 8 bit channels) is a uniform colour: the card's
/// own "no signal" picture or a sleeping display, never a screen worth reading.
let minimum_brightness_range = 24
/// Every this many pixels across and down is sampled: about half a million
/// samples of a 4K frame, dense enough to land on the sparse text of a dark
/// terminal, where a coarse grid sees only background and calls it blank.
let brightness_sample_stride = 4

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

enum Request {
    case capture(output_path: String, device_name: String)
    case check_permission
    case list_devices
}

let usage_message = "usage: hdmi_capture --output <path.png> --device <name> | --list-devices | --check-permission"

func parse_arguments() -> Request {
    let arguments = Array(CommandLine.arguments.dropFirst())
    if arguments == ["--list-devices"] { return .list_devices }
    if arguments == ["--check-permission"] { return .check_permission }
    return parse_capture_arguments(arguments)
}

func parse_capture_arguments(_ given: [String]) -> Request {
    var output_path = ""
    var device_name = ""
    var arguments = given
    while let flag = arguments.first {
        arguments.removeFirst()
        guard let value = arguments.first else { die(usage_message) }
        arguments.removeFirst()
        switch flag {
        case "--device": device_name = value
        case "--output": output_path = value
        default: die(usage_message)
        }
    }
    guard !output_path.isEmpty, !device_name.isEmpty else { die(usage_message) }
    return .capture(output_path: output_path, device_name: device_name)
}

// MARK: - devices

/// Capture cards, webcams and the like; the built in camera is not external.
func external_video_devices() -> [AVCaptureDevice] {
    return AVCaptureDevice.DiscoverySession(deviceTypes: [.external], mediaType: .video,
                                            position: .unspecified).devices
}

/// The name comes from configuration, so it must match exactly: a near miss
/// could be a different camera, and photographing a person instead of a screen
/// is not a mistake to make quietly.
func device_named(_ name: String) -> AVCaptureDevice {
    let devices = external_video_devices()
    guard let device = devices.first(where: { $0.localizedName == name }) else {
        let attached = devices.map { $0.localizedName }.joined(separator: ", ")
        die("no video device named \(name) is attached; attached: "
            + (attached.isEmpty ? "none" : attached))
    }
    return device
}

func dimensions(of format: AVCaptureDevice.Format) -> CMVideoDimensions {
    return CMVideoFormatDescriptionGetDimensions(format.formatDescription)
}

/// The card offers several sizes and scales the signal to whichever is chosen,
/// so the largest one keeps the most of the other screen's text legible.
func select_the_largest_format(of device: AVCaptureDevice) {
    let area = { (format: AVCaptureDevice.Format) -> Int in
        let size = dimensions(of: format)
        return Int(size.width) * Int(size.height)
    }
    guard let largest = device.formats.max(by: { area($0) < area($1) }) else {
        die("\(device.localizedName) offers no video formats")
    }
    do {
        try device.lockForConfiguration()
    } catch {
        die("cannot configure \(device.localizedName) (\(error.localizedDescription))")
    }
    device.activeFormat = largest
    device.unlockForConfiguration()
    let size = dimensions(of: largest)
    log_ok("reading \(device.localizedName) at \(size.width)x\(size.height)")
}

// MARK: - permission

func name_of(_ status: AVAuthorizationStatus) -> String {
    switch status {
    case .authorized:    return "authorized"
    case .denied:        return "denied"
    case .notDetermined: return "not determined"
    case .restricted:    return "restricted by a policy on this Mac"
    @unknown default:    return "reported by macOS as \(status.rawValue)"
    }
}

/// macOS raises the prompt in the application this helper was launched from
/// and answers on another thread, so this blocks until the user has decided.
func ask_for_camera_access() -> Bool {
    let waiter = DispatchSemaphore(value: 0)
    var granted = false
    AVCaptureDevice.requestAccess(for: .video) { granted = $0; waiter.signal() }
    waiter.wait()
    return granted
}

/// Without this check a denied camera simply delivers no frames, which would
/// look like a missing HDMI signal. Only --check-permission may raise the prompt.
func require_camera_permission(may_ask: Bool) {
    let status = AVCaptureDevice.authorizationStatus(for: .video)
    switch status {
    case .authorized:
        return
    case .notDetermined where may_ask:
        if ask_for_camera_access() { return }
        die("the camera permission prompt was answered with Don't Allow")
    case .notDetermined:
        die("camera access, which reading the HDMI capture card needs, has not been granted "
            + "yet; run util/init_permissions.sh with the card attached to be asked for it")
    default:
        die("camera access, which reading the HDMI capture card needs, is \(name_of(status)); "
            + "grant it to rvw in System Settings, Privacy and Security, Camera, or run "
            + "util/init_permissions.sh -reset to be asked again")
    }
}

// MARK: - one frame

/// Keeps the first frame after the card has settled and signals the waiter.
final class FrameGrabber: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let waiter = DispatchSemaphore(value: 0)
    private var frames_seen = 0
    private(set) var frame: CVPixelBuffer?

    func captureOutput(_ output: AVCaptureOutput, didOutput sample_buffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard frame == nil else { return }
        frames_seen += 1
        guard frames_seen > frames_to_settle,
              let pixels = CMSampleBufferGetImageBuffer(sample_buffer) else { return }
        frame = pixels
        waiter.signal()
    }

    func wait_for_the_frame(from device_name: String) -> CVPixelBuffer {
        guard waiter.wait(timeout: .now() + frame_timeout_seconds) == .success,
              let frame else {
            die("\(device_name) delivered no frame within \(Int(frame_timeout_seconds))s; is "
                + "the other Mac's HDMI output connected to it and its display awake?")
        }
        return frame
    }
}

/// BGRA, so the frame can be checked byte by byte and handed to CoreImage as is.
func video_output(delivering_to grabber: FrameGrabber) -> AVCaptureVideoDataOutput {
    let output = AVCaptureVideoDataOutput()
    output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]
    output.alwaysDiscardsLateVideoFrames = true
    output.setSampleBufferDelegate(grabber, queue: DispatchQueue(label: "ai.rvw.hdmi_capture"))
    return output
}

func capture_session(for device: AVCaptureDevice, output: AVCaptureVideoDataOutput) -> AVCaptureSession {
    let session = AVCaptureSession()
    session.beginConfiguration()
    do {
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else { die("cannot read from \(device.localizedName)") }
        session.addInput(input)
    } catch {
        die("cannot open \(device.localizedName) (\(error.localizedDescription))")
    }
    guard session.canAddOutput(output) else { die("cannot receive frames from \(device.localizedName)") }
    session.addOutput(output)
    session.commitConfiguration()
    // The session falls back to its preset unless the format is chosen once the
    // device is part of it.
    select_the_largest_format(of: device)
    return session
}

func grab_one_frame(from device: AVCaptureDevice) -> CVPixelBuffer {
    let grabber = FrameGrabber()
    let session = capture_session(for: device, output: video_output(delivering_to: grabber))
    session.startRunning()
    let frame = grabber.wait_for_the_frame(from: device.localizedName)
    session.stopRunning()
    return frame
}

// MARK: - checking the frame

/// The difference between the brightest and the darkest of the sampled pixels.
func brightness_range(of frame: CVPixelBuffer) -> Int {
    CVPixelBufferLockBaseAddress(frame, .readOnly)
    defer { CVPixelBufferUnlockBaseAddress(frame, .readOnly) }
    guard let base = CVPixelBufferGetBaseAddress(frame) else { die("the frame has no pixels") }
    let bytes = base.assumingMemoryBound(to: UInt8.self)
    let row_bytes = CVPixelBufferGetBytesPerRow(frame)
    var darkest = Int.max
    var brightest = Int.min
    for y in stride(from: 0, to: CVPixelBufferGetHeight(frame), by: brightness_sample_stride) {
        for x in stride(from: 0, to: CVPixelBufferGetWidth(frame), by: brightness_sample_stride) {
            let pixel = y * row_bytes + x * 4
            let brightness = Int(bytes[pixel]) + Int(bytes[pixel + 1]) + Int(bytes[pixel + 2])
            darkest = min(darkest, brightness)
            brightest = max(brightest, brightness)
        }
    }
    return brightest - darkest
}

func require_a_picture_in(_ frame: CVPixelBuffer, from device_name: String) {
    guard brightness_range(of: frame) >= minimum_brightness_range else {
        die("\(device_name) delivered a frame of one uniform colour; is the other Mac's "
            + "HDMI output connected to it, its display awake and mirrored to it, and is "
            + "there anything on that screen?")
    }
}

// MARK: - output

func cg_image(of frame: CVPixelBuffer) -> CGImage {
    let image = CIImage(cvPixelBuffer: frame)
    guard let converted = CIContext().createCGImage(image, from: image.extent) else {
        die("cannot convert the captured frame into an image")
    }
    return converted
}

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

func print_metadata(_ metadata: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: metadata, options: [.sortedKeys]),
          let text = String(data: data, encoding: .utf8) else {
        die("cannot serialize the capture metadata")
    }
    print(text)
}

// MARK: - entry point

func capture(to output_path: String, from device_name: String) {
    let device = device_named(device_name)
    require_camera_permission(may_ask: false)
    let frame = grab_one_frame(from: device)
    require_a_picture_in(frame, from: device_name)
    let image = cg_image(of: frame)
    write_png(image, to: output_path)
    log_ok("captured the frame from \(device_name)")
    print_metadata(["target": "hdmi", "device": device_name,
                    "width": image.width, "height": image.height])
}

@main
struct HdmiCaptureTool {
    static func main() {
        switch parse_arguments() {
        case .capture(let output_path, let device_name):
            capture(to: output_path, from: device_name)
        case .check_permission:
            require_camera_permission(may_ask: true)
            log_ok("camera access is authorized")
        case .list_devices:
            for device in external_video_devices() { print(device.localizedName) }
        }
    }
}

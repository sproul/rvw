// Capture one audio stream and write it to stdout as mono 16 kHz float32 PCM.
//
//   audio_capture --source mic       microphone, via AVAudioEngine
//   audio_capture --source system    everything the Mac plays back, via a
//                                    Core Audio process tap
//
// The process tap is observational: it does not sit in the playback path, so
// speakers and headphones keep working exactly as before and no virtual audio
// device has to be installed. The two sources are deliberately separate
// processes so that my own voice and the far end never end up mixed into a
// single stream.
//
// Diagnostics go to stderr, one line each, prefixed with "OK " or "FAIL ".

import AVFoundation
import CoreAudio
import Darwin
import Foundation

let target_sample_rate = 16000.0

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

func require_no_error(_ status: OSStatus, _ what: String) {
    if status != noErr { die("\(what) failed with Core Audio status \(status)") }
}

// MARK: - stdout writer

/// Serialises PCM writes so that audio callbacks never block on the pipe.
final class PcmStdoutWriter {
    private let queue = DispatchQueue(label: "rvw.pcm.writer")
    private let descriptor = FileHandle.standardOutput.fileDescriptor

    func write(_ samples: UnsafePointer<Float>, _ frame_count: Int) {
        guard frame_count > 0 else { return }
        let payload = Data(bytes: samples, count: frame_count * MemoryLayout<Float>.size)
        queue.async { self.write_all(payload) }
    }

    private func write_all(_ payload: Data) {
        payload.withUnsafeBytes { raw in
            var offset = 0
            while offset < raw.count {
                let written = Darwin.write(descriptor, raw.baseAddress!.advanced(by: offset),
                                           raw.count - offset)
                if written <= 0 { AudioCaptureTool.shutdown_and_exit() }   // assistant closed the pipe
                offset += written
            }
        }
    }
}

// MARK: - resampling

/// Converts whatever the hardware produces into mono 16 kHz float32.
final class MonoResampler {
    private let converter: AVAudioConverter
    private let output_format: AVAudioFormat
    private let writer: PcmStdoutWriter

    init(input_format: AVAudioFormat, writer: PcmStdoutWriter) {
        guard let output_format = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                                sampleRate: target_sample_rate,
                                                channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: input_format, to: output_format) else {
            die("cannot convert \(input_format) to mono \(Int(target_sample_rate)) Hz float32")
        }
        self.output_format = output_format
        self.converter = converter
        self.writer = writer
    }

    func convert_and_emit(_ input: AVAudioPCMBuffer) {
        guard input.frameLength > 0, let output = make_output_buffer(for: input) else { return }
        var error: NSError?
        var already_supplied = false
        converter.convert(to: output, error: &error) { _, status in
            if already_supplied { status.pointee = .noDataNow; return nil }
            already_supplied = true
            status.pointee = .haveData
            return input
        }
        if let error = error {
            log_fail("resampling: \(error.localizedDescription)")
            return
        }
        if let channel = output.floatChannelData { writer.write(channel[0], Int(output.frameLength)) }
    }

    private func make_output_buffer(for input: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        let ratio = output_format.sampleRate / input.format.sampleRate
        let capacity = AVAudioFrameCount(Double(input.frameLength) * ratio) + 1024
        return AVAudioPCMBuffer(pcmFormat: output_format, frameCapacity: capacity)
    }
}

// MARK: - microphone

final class MicrophoneCapture {
    private let engine = AVAudioEngine()
    private let writer: PcmStdoutWriter
    private var resampler: MonoResampler?

    init(writer: PcmStdoutWriter) { self.writer = writer }

    /// Without this check a denied microphone simply delivers silence for ever.
    private func require_microphone_permission() {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            return
        case .notDetermined:
            let waiter = DispatchSemaphore(value: 0)
            var granted = false
            AVCaptureDevice.requestAccess(for: .audio) { granted = $0; waiter.signal() }
            waiter.wait()
            if granted { return }
        default:
            break
        }
        die("microphone access is denied; grant it to the application running this helper "
            + "in System Settings, Privacy and Security, Microphone")
    }

    func start() {
        require_microphone_permission()
        let input_node = engine.inputNode
        let input_format = input_node.outputFormat(forBus: 0)
        guard input_format.sampleRate > 0 else { die("no microphone input is available") }
        let resampler = MonoResampler(input_format: input_format, writer: writer)
        self.resampler = resampler
        input_node.installTap(onBus: 0, bufferSize: 4096, format: input_format) { buffer, _ in
            resampler.convert_and_emit(buffer)
        }
        do {
            try engine.start()
        } catch {
            die("cannot start the audio engine: \(error.localizedDescription)")
        }
        log_ok("capturing the microphone at \(Int(input_format.sampleRate)) Hz")
    }
}

// MARK: - system audio

/// Reads a Core Audio property whose value is a single fixed size value.
func read_audio_property<T>(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector,
                            _ fallback: T) -> T {
    var address = AudioObjectPropertyAddress(mSelector: selector,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var value = fallback
    var size = UInt32(MemoryLayout<T>.size)
    require_no_error(AudioObjectGetPropertyData(object, &address, 0, nil, &size, &value),
                     "reading Core Audio property \(selector)")
    return value
}

func default_output_device_uid() -> String {
    let device: AudioDeviceID = read_audio_property(AudioObjectID(kAudioObjectSystemObject),
                                                    kAudioHardwarePropertyDefaultOutputDevice,
                                                    AudioDeviceID(kAudioObjectUnknown))
    guard device != kAudioObjectUnknown else { die("there is no default output device") }
    let uid: CFString = read_audio_property(device, kAudioDevicePropertyDeviceUID, "" as CFString)
    return uid as String
}

func tap_stream_format(_ tap: AudioObjectID) -> AVAudioFormat {
    var address = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyFormat,
                                             mScope: kAudioObjectPropertyScopeGlobal,
                                             mElement: kAudioObjectPropertyElementMain)
    var description = AudioStreamBasicDescription()
    var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    require_no_error(AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &description),
                     "reading the tap stream format")
    guard let format = AVAudioFormat(streamDescription: &description) else {
        die("the tap produced an unsupported stream format")
    }
    return format
}

final class SystemAudioCapture {
    private let writer: PcmStdoutWriter
    private var tap_description: CATapDescription?
    private var tap = AudioObjectID(kAudioObjectUnknown)
    private var aggregate_device = AudioDeviceID(kAudioObjectUnknown)
    private var io_proc: AudioDeviceIOProcID?
    private var resampler: MonoResampler?

    init(writer: PcmStdoutWriter) { self.writer = writer }

    func start() {
        create_global_tap()
        create_private_aggregate_device()
        let format = tap_stream_format(tap)
        resampler = MonoResampler(input_format: format, writer: writer)
        install_io_proc(format: format)
        require_no_error(AudioDeviceStart(aggregate_device, io_proc), "starting the tap device")
        log_ok("capturing system audio at \(Int(format.sampleRate)) Hz, "
               + "\(format.channelCount) channel(s)")
    }

    /// Releases the tap and the private device; leaving either behind confuses Core Audio.
    func stop() {
        if let io_proc = io_proc {
            AudioDeviceStop(aggregate_device, io_proc)
            AudioDeviceDestroyIOProcID(aggregate_device, io_proc)
            self.io_proc = nil
        }
        if aggregate_device != kAudioObjectUnknown {
            AudioHardwareDestroyAggregateDevice(aggregate_device)
            aggregate_device = AudioDeviceID(kAudioObjectUnknown)
        }
        if tap != kAudioObjectUnknown {
            AudioHardwareDestroyProcessTap(tap)
            tap = AudioObjectID(kAudioObjectUnknown)
        }
    }

    /// A global tap with an empty exclusion list observes every process.
    private func create_global_tap() {
        let description = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        description.isPrivate = true
        description.muteBehavior = .unmuted
        tap_description = description
        let status = AudioHardwareCreateProcessTap(description, &tap)
        if status != noErr {
            die("cannot create the system audio tap (status \(status)); the terminal running "
                + "this helper needs permission to record system audio")
        }
    }

    /// A private aggregate device is the only way to read a tap; it is not published
    /// to other applications and does not become anyone's output device.
    private func create_private_aggregate_device() {
        let output_uid = default_output_device_uid()
        guard let tap_uid = tap_description?.uuid.uuidString else { die("the tap has no uid") }
        let description: [String: Any] = [
            kAudioAggregateDeviceNameKey: "rvw system capture",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: output_uid,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: output_uid]],
            kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                               kAudioSubTapUIDKey: tap_uid]],
        ]
        require_no_error(AudioHardwareCreateAggregateDevice(description as CFDictionary,
                                                            &aggregate_device),
                         "creating the private aggregate capture device")
    }

    private func install_io_proc(format: AVAudioFormat) {
        let queue = DispatchQueue(label: "rvw.system.capture")
        let status = AudioDeviceCreateIOProcIDWithBlock(&io_proc, aggregate_device, queue) {
            [weak self] _, input_data, _, _, _ in
            guard let resampler = self?.resampler,
                  let buffer = AVAudioPCMBuffer(pcmFormat: format,
                                                bufferListNoCopy: input_data,
                                                deallocator: nil) else { return }
            resampler.convert_and_emit(buffer)
        }
        require_no_error(status, "installing the capture callback")
    }
}

// MARK: - entry point

@main
struct AudioCaptureTool {
    // Held statically so that the C signal handlers, which cannot capture context,
    // can still shut the tap down cleanly.
    static var microphone_capture: MicrophoneCapture?
    static var system_capture: SystemAudioCapture?

    static func main() {
        let writer = PcmStdoutWriter()
        if parse_source_argument() == "mic" {
            microphone_capture = MicrophoneCapture(writer: writer)
            microphone_capture?.start()
        } else {
            system_capture = SystemAudioCapture(writer: writer)
            system_capture?.start()
        }
        install_termination_handlers()
        RunLoop.main.run()
    }

    static func parse_source_argument() -> String {
        let arguments = Array(CommandLine.arguments.dropFirst())
        guard arguments.count == 2, arguments[0] == "--source" else {
            die("usage: audio_capture --source mic|system")
        }
        guard arguments[1] == "mic" || arguments[1] == "system" else {
            die("unknown source \(arguments[1]); expected mic or system")
        }
        return arguments[1]
    }

    static func install_termination_handlers() {
        signal(SIGPIPE, SIG_IGN)
        signal(SIGINT) { _ in AudioCaptureTool.shutdown_and_exit() }
        signal(SIGTERM) { _ in AudioCaptureTool.shutdown_and_exit() }
    }

    static func shutdown_and_exit() -> Never {
        system_capture?.stop()
        exit(0)
    }
}

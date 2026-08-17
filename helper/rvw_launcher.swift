// The executable inside bin/rvw.app: it starts one program from the repository
// and waits for it.
//
// macOS grants the microphone, system audio and screen recording to the
// application responsible for the process that asks, never to the helper doing
// the asking. Started from a terminal the assistant borrowed that terminal's
// permissions, so every terminal, editor and IDE had to be granted separately,
// and Emacs.app could not be granted the microphone at all: it is signed with
// the hardened runtime and without com.apple.security.device.audio-input, so
// macOS refuses without ever asking. Launched through this bundle the assistant
// is responsible for itself. One grant, made once, and it no longer matters who
// starts it.
//
// THIS FILE MUST NOT CHANGE. An ad hoc signature is pinned to the exact bytes
// of the binary it signs:
//
//     # designated => cdhash H"3e6066c51eb7fef93e36317f1165c2a7a7b79077"
//
// so rebuilding this launcher gives rvw.app a new identity and macOS forgets
// every permission ever granted to it. Everything that is expected to change
// lives outside the bundle, where changing it costs nothing: the python daemon,
// both capture helpers and every shell script. This launcher only has to find
// them and run them.
//
//   rvw_launcher [arguments...]           starts bin/rvw -here with the arguments
//   rvw_launcher --run NAME [arguments]   starts bin/NAME with the arguments
//
// The bundle only becomes the responsible application when LaunchServices
// starts it, which means "open -n -a bin/rvw.app --args ...". Run this binary
// straight from a shell and the shell's application stays responsible and the
// grants go back where they came from, so bin/rvw and util/init_permissions.sh
// both go through open.
//
// Launched through LaunchServices there is no terminal to write to, so the
// program's output goes to var/log/NAME.launcher.log.

import Foundation

let bundle_directory_name = "rvw.app"
let daemon_program = "rvw"
let daemon_in_this_process_flag = "-here"

// MARK: - diagnostics

/// Set once the log file is open; before that diagnostics reach stderr alone.
var log_file: FileHandle?

func write_diagnostic(_ line: String) {
    let data = (line + "\n").data(using: .utf8)!
    FileHandle.standardError.write(data)
    log_file?.write(data)
}

func log_ok(_ message: String)   { write_diagnostic("OK   " + message) }
func log_fail(_ message: String) { write_diagnostic("FAIL " + message) }

func die(_ message: String) -> Never {
    log_fail(message)
    exit(1)
}

// MARK: - where everything is

/// The bundle sits at <repository>/bin/rvw.app, so the repository is two
/// directories above it. Nothing here is compiled in, which is what lets the
/// checkout be moved or renamed without rebuilding and losing the grants.
func repository_directory() -> URL {
    let bundle_url = Bundle.main.bundleURL
    guard bundle_url.lastPathComponent == bundle_directory_name else {
        die("this launcher belongs inside \(bundle_directory_name), not \(bundle_url.path)")
    }
    return bundle_url.deletingLastPathComponent().deletingLastPathComponent()
}

/// The program name arrives on a command line and names a file in one fixed
/// directory, so anything holding a path separator is either a mistake or an
/// attempt to escape that directory. Both are refused rather than repaired.
func program_path(_ name: String, in bin_directory: URL) -> URL {
    guard !name.isEmpty, !name.contains("/"), name != ".", name != ".." else {
        die("refusing to run \(name); --run takes the name of a program in bin, not a path")
    }
    let path = bin_directory.appendingPathComponent(name)
    guard FileManager.default.isExecutableFile(atPath: path.path) else {
        die("there is no executable named \(name) in \(bin_directory.path)")
    }
    return path
}

/// One log per program, emptied at each launch, so whoever started the program
/// reads this run and not the last one. Opened for appending because the parent
/// and the child both write to it.
func open_launcher_log(for program: String, in repository: URL) -> FileHandle {
    let directory = repository.appendingPathComponent("var/log")
    try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    let path = directory.appendingPathComponent("\(program).launcher.log").path
    let descriptor = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_APPEND, 0o644)
    guard descriptor >= 0 else { die("cannot write the launcher log at \(path)") }
    return FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
}

// MARK: - command line

struct LaunchRequest {
    let program: String
    let arguments: [String]
    /// Everything in bin/ writes its diagnostics to stderr and its output to
    /// stdout, and for the capture helpers that output is a stream of audio
    /// samples or the bytes of an image. Only the assistant's own stdout is
    /// words, so only the assistant's is kept; the rest would fill the log with
    /// the recording itself.
    let stdout_is_a_data_stream: Bool
}

/// Without --run the launcher starts the assistant itself, telling bin/rvw to
/// run in this process rather than launching the bundle it is already inside.
/// --run exists for util/init_permissions.sh, which exercises one capture
/// helper at a time to find out which permissions macOS has granted.
func parse_arguments() -> LaunchRequest {
    var arguments = Array(CommandLine.arguments.dropFirst())
    guard arguments.first == "--run" else {
        return LaunchRequest(program: daemon_program,
                             arguments: [daemon_in_this_process_flag] + arguments,
                             stdout_is_a_data_stream: false)
    }
    arguments.removeFirst()
    guard let program = arguments.first else {
        die("--run needs the name of a program in bin")
    }
    arguments.removeFirst()
    return LaunchRequest(program: program, arguments: arguments,
                         stdout_is_a_data_stream: true)
}

// MARK: - entry point

@main
struct RvwLauncher {
    // Held statically so that the C signal handlers, which cannot capture
    // context, can still stop the program this launcher started.
    static var child_process_id: pid_t = 0

    static func main() {
        let request = parse_arguments()
        let repository = repository_directory()
        log_file = open_launcher_log(for: request.program, in: repository)
        let executable = program_path(request.program,
                                      in: repository.appendingPathComponent("bin"))
        exit(run_and_wait(executable, request, in: repository))
    }

    /// The program is a child rather than an exec of this process: TCC blames
    /// the responsible application, and staying alive as that application is
    /// this launcher's only job.
    static func run_and_wait(_ executable: URL, _ request: LaunchRequest, in repository: URL) -> Int32 {
        let program = Process()
        program.executableURL = executable
        program.arguments = request.arguments
        program.currentDirectoryURL = repository
        program.standardOutput = request.stdout_is_a_data_stream ? FileHandle.nullDevice : log_file
        program.standardError = log_file
        do {
            try program.run()
        } catch {
            die("cannot start \(executable.path): \(error.localizedDescription)")
        }
        child_process_id = program.processIdentifier
        install_termination_handlers()
        log_ok("started \(executable.lastPathComponent) as pid \(child_process_id)")
        program.waitUntilExit()
        return report_exit(of: executable.lastPathComponent, program.terminationStatus)
    }

    /// Whoever is watching the log needs to know the program stopped, otherwise
    /// a program that dies without a word looks like one still starting up.
    static func report_exit(of program: String, _ status: Int32) -> Int32 {
        if status == 0 {
            log_ok("\(program) exited with status 0")
        } else {
            log_fail("\(program) exited with status \(status)")
        }
        return status
    }

    static func install_termination_handlers() {
        signal(SIGINT)  { _ in RvwLauncher.stop_the_child_and_exit() }
        signal(SIGTERM) { _ in RvwLauncher.stop_the_child_and_exit() }
    }

    /// Only calls that are safe inside a signal handler: kill and _exit.
    static func stop_the_child_and_exit() -> Never {
        if child_process_id > 0 { kill(child_process_id, SIGTERM) }
        _exit(0)
    }
}

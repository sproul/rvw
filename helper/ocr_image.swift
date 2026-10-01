// OCR one already saved image with the built-in Vision framework.
//
// Usage: ocr_image <image-path>
//
// The image comes from a file rvw already archived, so this helper needs no
// screen recording permission of its own: it is a plain reader. Recognised
// text goes to stdout, one line per observation; a failure says FAIL on
// stderr and exits nonzero so the caller cannot mistake it for a blank image.
// Language correction is off on purpose: for code and diagrams a plausible
// "corrected" word is worse than the letters that were actually there.

import Foundation
import Vision

func fail(_ message: String) -> Never {
    FileHandle.standardError.write("FAIL \(message)\n".data(using: .utf8)!)
    Foundation.exit(1)
}

@main
struct OcrImage {
    static func main() {
        guard CommandLine.arguments.count == 2 else {
            fail("usage: ocr_image <image-path>")
        }
        let image_url = URL(fileURLWithPath: CommandLine.arguments[1])
        guard FileManager.default.fileExists(atPath: image_url.path) else {
            fail("no image at \(image_url.path)")
        }

        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = false

        do {
            try VNImageRequestHandler(url: image_url).perform([request])
        } catch {
            fail("OCR of \(image_url.lastPathComponent) failed: \(error.localizedDescription)")
        }
        let lines = (request.results ?? []).compactMap {
            $0.topCandidates(1).first?.string
        }
        print(lines.joined(separator: "\n"))
    }
}

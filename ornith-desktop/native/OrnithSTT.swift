// On-device speech recognition helper for Ornith Desktop.
//
// Usage:  ornith-stt <path-to-wav> [locale]
// Output: a single JSON object on stdout: {"text":"..."} or {"error":"..."}
//
// requiresOnDeviceRecognition is set to true, so audio is never sent to Apple's
// servers. If the on-device model for the locale is unavailable, this fails
// with a clear error rather than silently falling back to network recognition.

import Foundation
import Speech

struct Output: Codable {
    var text: String?
    var error: String?
}

func emit(_ output: Output) -> Never {
    let encoder = JSONEncoder()
    if let data = try? encoder.encode(output), let json = String(data: data, encoding: .utf8) {
        print(json)
    } else {
        print("{\"error\":\"Failed to encode result\"}")
    }
    exit(output.error == nil ? 0 : 1)
}

let arguments = CommandLine.arguments
guard arguments.count >= 2 else {
    emit(Output(error: "Usage: ornith-stt <path-to-wav> [locale]"))
}

let audioPath = arguments[1]
let localeIdentifier = arguments.count >= 3 ? arguments[2] : "en-US"

guard FileManager.default.fileExists(atPath: audioPath) else {
    emit(Output(error: "Audio file not found: \(audioPath)"))
}

guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeIdentifier)) else {
    emit(Output(error: "No speech recogniser for locale \(localeIdentifier)"))
}

// Authorisation. In a packaged app the prompt is attributed to the host app,
// which must carry NSSpeechRecognitionUsageDescription in its Info.plist.
let authSemaphore = DispatchSemaphore(value: 0)
var authStatus: SFSpeechRecognizerAuthorizationStatus = .notDetermined
SFSpeechRecognizer.requestAuthorization { status in
    authStatus = status
    authSemaphore.signal()
}
_ = authSemaphore.wait(timeout: .now() + 30)

switch authStatus {
case .authorized:
    break
case .denied:
    emit(Output(error: "Speech recognition permission was denied in System Settings > Privacy & Security > Speech Recognition."))
case .restricted:
    emit(Output(error: "Speech recognition is restricted on this Mac."))
case .notDetermined:
    emit(Output(error: "Speech recognition permission was not granted."))
@unknown default:
    emit(Output(error: "Unknown speech recognition authorisation status."))
}

guard recognizer.isAvailable else {
    emit(Output(error: "The speech recogniser is not currently available."))
}

guard recognizer.supportsOnDeviceRecognition else {
    emit(Output(error: "On-device recognition is unavailable for \(localeIdentifier). Add the language under System Settings > Keyboard > Dictation to enable it offline."))
}

let request = SFSpeechURLRecognitionRequest(url: URL(fileURLWithPath: audioPath))
// The whole point: keep the audio on this machine.
request.requiresOnDeviceRecognition = true
request.shouldReportPartialResults = false

let recognitionSemaphore = DispatchSemaphore(value: 0)
var result = Output()

recognizer.recognitionTask(with: request) { recognitionResult, error in
    if let error = error {
        result.error = error.localizedDescription
        recognitionSemaphore.signal()
        return
    }
    guard let recognitionResult = recognitionResult else { return }
    if recognitionResult.isFinal {
        result.text = recognitionResult.bestTranscription.formattedString
        recognitionSemaphore.signal()
    }
}

if recognitionSemaphore.wait(timeout: .now() + 60) == .timedOut {
    emit(Output(error: "Speech recognition timed out."))
}

emit(result)

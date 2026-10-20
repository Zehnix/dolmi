// dolmi-audio — streams everything the Mac plays (all apps, any output device) to stdout.
//
// macOS 14.2+ Core Audio process tap: the Mac counterpart of WASAPI loopback on Windows. You keep
// hearing the meeting, no virtual audio cable is needed, and there is no screen-recording indicator.
// The first launch asks for "System Audio Recording" permission (Privacy & Security).
//
// Output: one text line "DOLMI-AUDIO <sample rate> 1\n", then raw little-endian float32 mono samples.
// Stops when stdout closes (Dolmi stopped listening), when Dolmi exits or crashes, or on SIGTERM/SIGINT.
//
// VOICE-6 "English only": `dolmi-audio --mute-apps com.microsoft.teams,us.zoom` taps only the processes
// whose bundle ID starts with one of the prefixes (case-insensitive, so helpers such as
// "com.google.Chrome.helper" match "com.google.Chrome") with muteBehavior .mutedWhenTapped: Dolmi gets
// the meeting audio and the meeting is silent on the speakers/headphones only while Dolmi reads the tap.
// If Dolmi (or this helper) dies, macOS un-mutes the app by itself. The process list is re-scanned every
// 2 s (Teams/WebView/Chrome helper PIDs come and go); until a matching app exists, silence is streamed.
//
// Build: swiftc -O dolmi-audio.swift -o dolmi-audio
import AudioToolbox
import CoreAudio
import Foundation

setvbuf(stdout, nil, _IONBF, 0)
signal(SIGPIPE, SIG_IGN)

func fail(_ msg: String, _ status: OSStatus = 0) -> Never {
    FileHandle.standardError.write("dolmi-audio: \(msg)\(status != 0 ? " (OSStatus \(status))" : "")\n".data(using: .utf8)!)
    exit(1)
}

func prop<T>(_ obj: AudioObjectID, _ selector: AudioObjectPropertySelector, _ value: inout T) -> OSStatus {
    var addr = AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<T>.size)
    return withUnsafeMutablePointer(to: &value) { AudioObjectGetPropertyData(obj, &addr, 0, nil, &size, $0) }
}

// --mute-apps <comma-separated bundle ID prefixes>: tap and mute only those apps (VOICE-6)
func muteApps() -> [String]? {
    let args = CommandLine.arguments
    guard let i = args.firstIndex(of: "--mute-apps") else { return nil }
    guard i + 1 < args.count else { fail("--mute-apps needs a list of bundle IDs") }
    let list = args[i + 1].split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
                          .filter { !$0.isEmpty }
    if list.isEmpty { fail("--mute-apps needs a list of bundle IDs") }
    return list
}
let mutePrefixes = muteApps()

// Core Audio process objects whose bundle ID starts with one of the prefixes (never this helper itself)
func matchingProcesses(_ prefixes: [String]) -> [AudioObjectID] {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyProcessObjectList,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    let system = AudioObjectID(kAudioObjectSystemObject)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(system, &addr, 0, nil, &size) == noErr, size > 0 else { return [] }
    var objs = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
    guard AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &objs) == noErr else { return [] }
    let me = getpid()
    var found: [AudioObjectID] = []
    for obj in objs {
        var pid: pid_t = -1
        if prop(obj, kAudioProcessPropertyPID, &pid) != noErr || pid == me { continue }
        var ref: Unmanaged<CFString>?
        guard prop(obj, kAudioProcessPropertyBundleID, &ref) == noErr, let ref else { continue }
        let bundle = (ref.takeRetainedValue() as String).lowercased()
        if prefixes.contains(where: { bundle.hasPrefix($0) }) { found.append(obj) }
    }
    return found.sorted()
}

// 1. the tap: every process's mix, unmuted (default) - or only the meeting apps, muted while tapped
var tapID = AudioObjectID(kAudioObjectUnknown)
var aggID = AudioObjectID(kAudioObjectUnknown)
var procID: AudioDeviceIOProcID?
var tapped: [AudioObjectID] = []
var tap: CATapDescription!

func cleanup() {
    if aggID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggID) }
    if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
}

// 2. the tap is read through a private aggregate device clocked by the current output device
var outputID = AudioObjectID(kAudioObjectUnknown)
var st = prop(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDefaultSystemOutputDevice, &outputID)
if st != noErr { fail("no output device", st) }
var uidRef: Unmanaged<CFString>?
st = prop(outputID, kAudioDevicePropertyDeviceUID, &uidRef)
guard st == noErr, let uidRef else { fail("could not read the output device", st) }
let outputUID = uidRef.takeRetainedValue()

// The stream rate: the tap's format in the default mode. In --mute-apps mode the header has to go out
// before a meeting app may even be running, so it is the output device's rate (the aggregate's clock).
var rate: Float64 = 48000
let queue = DispatchQueue(label: "dolmi.audio")

func writeOrExit(_ mono: [Float]) {
    let ok = mono.withUnsafeBytes { fwrite($0.baseAddress, 1, $0.count, stdout) == $0.count }
    if !ok { cleanup(); exit(0) }                 // Dolmi closed the pipe: stop listening
}

func startTap() {
    st = AudioHardwareCreateProcessTap(tap, &tapID)
    if st != noErr { tapID = AudioObjectID(kAudioObjectUnknown); fail("could not create the system audio tap", st) }
    var format = AudioStreamBasicDescription()
    st = prop(tapID, kAudioTapPropertyFormat, &format)
    if st != noErr { cleanup(); fail("could not read the tap format", st) }
    if mutePrefixes == nil {
        if format.mSampleRate > 0 { rate = format.mSampleRate }
    } else if format.mSampleRate > 0 && format.mSampleRate != rate {
        FileHandle.standardError.write("dolmi-audio: tap runs at \(format.mSampleRate) Hz, stream says \(rate) Hz\n".data(using: .utf8)!)
    }

    let aggregate: [String: Any] = [
        kAudioAggregateDeviceNameKey: "Dolmi system audio",
        kAudioAggregateDeviceUIDKey: UUID().uuidString,
        kAudioAggregateDeviceMainSubDeviceKey: outputUID as String,
        kAudioAggregateDeviceIsPrivateKey: true,
        kAudioAggregateDeviceIsStackedKey: false,
        kAudioAggregateDeviceTapAutoStartKey: true,
        kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: outputUID as String]],
        kAudioAggregateDeviceTapListKey: [[kAudioSubTapDriftCompensationKey: true,
                                           kAudioSubTapUIDKey: tap.uuid.uuidString]],
    ]
    st = AudioHardwareCreateAggregateDevice(aggregate as CFDictionary, &aggID)
    if st != noErr { aggID = AudioObjectID(kAudioObjectUnknown); cleanup(); fail("could not create the capture device", st) }

    // 3. downmix every buffer to mono float32 and write it out
    st = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, queue) { _, inData, _, _, _ in
        let buffers = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inData))
        guard let first = buffers.first, let p0 = first.mData else { return }
        var mono: [Float]
        if buffers.count == 1 {                       // interleaved: n frames x channels
            let ch = Int(max(first.mNumberChannels, 1))
            let frames = Int(first.mDataByteSize) / (4 * ch)
            let s = p0.assumingMemoryBound(to: Float.self)
            mono = [Float](repeating: 0, count: frames)
            for i in 0..<frames {
                var sum: Float = 0
                for c in 0..<ch { sum += s[i * ch + c] }
                mono[i] = sum / Float(ch)
            }
        } else {                                      // one buffer per channel
            let frames = Int(first.mDataByteSize) / 4
            mono = [Float](repeating: 0, count: frames)
            for b in buffers {
                guard let d = b.mData else { continue }
                let s = d.assumingMemoryBound(to: Float.self)
                for i in 0..<frames { mono[i] += s[i] }
            }
            let n = Float(buffers.count)
            for i in 0..<frames { mono[i] /= n }
        }
        writeOrExit(mono)
    }
    if st != noErr { cleanup(); fail("could not attach to the capture device", st) }
    if mutePrefixes == nil {                      // header before the first samples
        FileHandle.standardOutput.write("DOLMI-AUDIO \(Int(rate)) 1\n".data(using: .utf8)!)
    }
    st = AudioDeviceStart(aggID, procID)
    if st != noErr { cleanup(); fail("could not start capturing", st) }
}

// point the running tap at a new process list (a Teams/Chrome helper started or quit)
func retarget(_ objs: [AudioObjectID]) {
    tap.processes = objs
    var addr = AudioObjectPropertyAddress(mSelector: kAudioTapPropertyDescription,
                                          mScope: kAudioObjectPropertyScopeGlobal,
                                          mElement: kAudioObjectPropertyElementMain)
    var ref = Unmanaged.passUnretained(tap!)
    let s = withUnsafeMutablePointer(to: &ref) {
        AudioObjectSetPropertyData(tapID, &addr, 0, nil, UInt32(MemoryLayout<Unmanaged<CATapDescription>>.size), $0)
    }
    if s != noErr {
        FileHandle.standardError.write("dolmi-audio: could not update the tapped apps (OSStatus \(s))\n".data(using: .utf8)!)
    }
}

var rescanTimer: DispatchSourceTimer?
if let prefixes = mutePrefixes {
    var nominal: Float64 = 0
    if prop(outputID, kAudioDevicePropertyNominalSampleRate, &nominal) == noErr, nominal > 0 { rate = nominal }
    FileHandle.standardOutput.write("DOLMI-AUDIO \(Int(rate)) 1\n".data(using: .utf8)!)
    let silence = [Float](repeating: 0, count: Int(rate / 10))
    let timer = DispatchSource.makeTimerSource(queue: queue)
    var ticks = 0
    timer.schedule(deadline: .now(), repeating: .milliseconds(100))
    timer.setEventHandler {
        if tapID == kAudioObjectUnknown { writeOrExit(silence) }   // no meeting app yet: keep the stream alive
        ticks += 1
        if ticks % 20 != 1 { return }                              // re-scan every 2 s
        let now = matchingProcesses(prefixes)
        if now.isEmpty || now == tapped { return }                 // keep the last tap while an app restarts
        tapped = now
        if tapID == kAudioObjectUnknown {
            tap = CATapDescription(stereoMixdownOfProcesses: now)
            tap.uuid = UUID()
            tap.name = "Dolmi English only"
            tap.isPrivate = true
            tap.muteBehavior = .mutedWhenTapped               // silent on the speakers only while Dolmi reads it
            startTap()
        } else {
            retarget(now)
        }
    }
    timer.resume()
    rescanTimer = timer
} else {
    tap = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
    tap.uuid = UUID()
    tap.name = "Dolmi"
    tap.isPrivate = true
    tap.muteBehavior = .unmuted                       // the user still hears everything
    startTap()
}

func stopAll() -> Never {
    rescanTimer?.cancel()
    if aggID != kAudioObjectUnknown, let procID { AudioDeviceStop(aggID, procID) }
    cleanup()
    exit(0)
}

for sig in [SIGTERM, SIGINT] {
    signal(sig, SIG_IGN)
    let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    src.setEventHandler { stopAll() }
    src.resume()
    _ = Unmanaged.passRetained(src)
}
// Dolmi's own children (e.g. multiprocessing's resource tracker) can keep the pipe open after Dolmi
// exits, so also watch the parent process itself: never keep capturing after Dolmi is gone.
let parent = getppid()
let parentWatch = DispatchSource.makeProcessSource(identifier: parent, eventMask: .exit, queue: .main)
parentWatch.setEventHandler { stopAll() }
parentWatch.resume()
if getppid() != parent || parent == 1 { stopAll() }   // parent died before the watch began

dispatchMain()

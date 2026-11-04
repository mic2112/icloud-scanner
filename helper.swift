// Native helper for cleaner.py. Subcommands:
//   similar          stdin: "uuidA\tpathA\tuuidB\tpathB" per line -> stdout: "uuidA\tuuidB\tdistance"
//   delete UUID...   moves Photos assets to Recently Deleted (macOS asks to confirm); exit 2 = no Photos access
//   trash PATH...    moves files to the Trash (Put Back works)
//   auth             prints Photos authorization status
import Foundation
import Photos
import Vision

func fail(_ msg: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write((msg + "\n").data(using: .utf8)!)
    exit(code)
}

func featurePrint(_ path: String) -> VNFeaturePrintObservation? {
    let req = VNGenerateImageFeaturePrintRequest()
    let handler = VNImageRequestHandler(url: URL(fileURLWithPath: path))
    try? handler.perform([req])
    return req.results?.first as? VNFeaturePrintObservation
}

func similar() {
    // ponytail: only the previous print is cached; pairs arrive in date order so each image is printed once.
    var cache: [String: VNFeaturePrintObservation] = [:]
    while let line = readLine() {
        let f = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
        guard f.count == 4 else { continue }
        let a = cache[f[0]] ?? featurePrint(f[1])
        let b = featurePrint(f[3])
        cache = [:]
        if let b { cache[f[2]] = b }
        var d: Float = -1
        if let a, let b { try? a.computeDistance(&d, to: b) }
        print("\(f[0])\t\(f[2])\t\(d)")
        fflush(stdout)
    }
}

func authorize() async -> PHAuthorizationStatus {
    let s = PHPhotoLibrary.authorizationStatus(for: .readWrite)
    return s == .notDetermined ? await PHPhotoLibrary.requestAuthorization(for: .readWrite) : s
}

func delete(_ uuids: [String]) async {
    guard await authorize() == .authorized else {
        fail("Photos access denied. Allow it in System Settings > Privacy & Security > Photos for your Terminal app.", code: 2)
    }
    let assets = PHAsset.fetchAssets(withLocalIdentifiers: uuids.map { "\($0)/L0/001" }, options: nil)
    guard assets.count > 0 else { fail("None of those items were found in Photos.") }
    do {
        try await PHPhotoLibrary.shared().performChanges { PHAssetChangeRequest.deleteAssets(assets) }
        print(assets.count)
    } catch {
        fail("Delete cancelled or failed: \(error.localizedDescription)")
    }
}

let args = Array(CommandLine.arguments.dropFirst())
switch args.first {
case "similar": similar()
case "delete": await delete(Array(args.dropFirst()))
case "trash":
    for p in args.dropFirst() {
        do { try FileManager.default.trashItem(at: URL(fileURLWithPath: p), resultingItemURL: nil) }
        catch { fail("Could not trash \(p): \(error.localizedDescription)") }
    }
    print(args.count - 1)
case "auth":
    let names: [PHAuthorizationStatus: String] = [.authorized: "authorized", .denied: "denied", .limited: "limited",
                                                  .notDetermined: "notDetermined", .restricted: "restricted"]
    print(names[PHPhotoLibrary.authorizationStatus(for: .readWrite)] ?? "unknown")
default: fail("usage: helper similar|delete UUID...|trash PATH...|auth")
}

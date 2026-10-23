import Foundation
import Vision
import CoreImage
import ImageIO
import UniformTypeIdentifiers
// Input must be upright, numbered CFR frames produced by person_mask.py.
let root = URL(fileURLWithPath: CommandLine.arguments[1])
let out = URL(fileURLWithPath: CommandLine.arguments[2])
let files = try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil).filter { $0.pathExtension == "png" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
let context = CIContext(options: [.cacheIntermediates: false])
let request = VNGeneratePersonSegmentationRequest()
request.qualityLevel = .accurate
request.outputPixelFormat = kCVPixelFormatType_OneComponent8
for (index, file) in files.enumerated() {
    try autoreleasepool {
        guard let src = CGImageSourceCreateWithURL(file as CFURL, nil), let image = CGImageSourceCreateImageAtIndex(src, 0, nil) else { throw NSError(domain:"mask",code:1) }
        try VNImageRequestHandler(cgImage:image, options:[:]).perform([request])
        guard let buffer = request.results?.first?.pixelBuffer else { throw NSError(domain:"mask",code:2) }
        let mask = CIImage(cvPixelBuffer: buffer)
        let scaled = mask.transformed(by: CGAffineTransform(scaleX:CGFloat(image.width)/mask.extent.width, y:CGFloat(image.height)/mask.extent.height))
        guard let cg = context.createCGImage(scaled, from:CGRect(x:0,y:0,width:image.width,height:image.height)), let dest = CGImageDestinationCreateWithURL(out.appendingPathComponent(file.lastPathComponent) as CFURL, UTType.png.identifier as CFString, 1, nil) else { throw NSError(domain:"mask",code:3) }
        CGImageDestinationAddImage(dest,cg,nil)
        guard CGImageDestinationFinalize(dest) else { throw NSError(domain:"mask",code:4) }
    }
    if index % 30 == 0 { fputs("mask \(index+1)/\(files.count)\n",stderr) }
}
print(files.count)

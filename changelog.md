# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2025-10-06

### Added

- Added eventlogging.
  
### Changed

- Bumped OpenOrchestrator to 2.*

## [1.2.4] - 2025-01-13

### Fixed

- Removed phone number from letter text.

## [1.2.3] - 2025-01-13

### Fixed

- Removed incomplete space-removing function
- Remove all spaces when testing for names in recipient list

## [1.2.2] - 2024-11-15

### Fixed

- Changed "(anmelder)" to "anmelder" when looking for receiver of letter.

## [1.2.1] - 2024-09-24

### Changed

- Notes are now passed through shared_components
- Robot now uses it's own set of credentials for logging into eFlyt

### Fixed

- Robot was too fast for reading dropdowns, now it's a bit slower and more stable.

## [1.2.0] - 2024-08-19

### Fixed

- Better handling of names containing dashes.

### Changed

 - Robot now uses Shared Components for Eflyt

## [1.1.2] - 2024-07-03

### Fixed

- Improved comparison of names with special characters.
- PDF errors are now caught, noted and skipped.

## [1.1.1] - 2024-01-23

### Added

- Changelog!
- Logivært name added to note.

### Fixed

- Name from PDF is sometimes formatted weird.

## [1.1.0] - 2024-01-03

### Added

- Check for Digital Post popup when sending letters.


## [1.0.0] - 2023-11-16

- Initial release

[1.2.3]: https://github.com/itk-dev-rpa/Rykning-paa-boligselskabs-og-logivaertssager-i-eFlyt/releases/tag/1.2.3
[1.2.2]: https://github.com/itk-dev-rpa/Rykning-paa-boligselskabs-og-logivaertssager-i-eFlyt/releases/tag/1.2.2
[1.2.1]: https://github.com/itk-dev-rpa/Rykning-paa-boligselskabs-og-logivaertssager-i-eFlyt/releases/tag/1.2.1
[1.2.0]: https://github.com/itk-dev-rpa/Rykning-paa-boligselskabs-og-logivaertssager-i-eFlyt/releases/tag/1.2.0
[1.1.2]: https://github.com/itk-dev-rpa/Rykning-paa-boligselskabs-og-logivaertssager-i-eFlyt/releases/tag/1.1.2
[1.1.1]: https://github.com/itk-dev-rpa/Rykning-paa-boligselskabs-og-logivaertssager-i-eFlyt/releases/tag/1.1.1
[1.1.0]: https://github.com/itk-dev-rpa/Rykning-paa-boligselskabs-og-logivaertssager-i-eFlyt/releases/tag/1.1.0
[1.0.0]: https://github.com/itk-dev-rpa/Rykning-paa-boligselskabs-og-logivaertssager-i-eFlyt/releases/tag/1.0.0

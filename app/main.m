//  Cổng — điểm vào của app. Một màn hình chính, phần còn lại đẩy vào từ đó.
#import "gw.h"

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@property (nonatomic, strong) UIWindow *window;
@end

@implementation AppDelegate

- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)opts {
    gwLog(@"app mở");
    self.window = [[UIWindow alloc] initWithFrame:[UIScreen mainScreen].bounds];
    HomeVC *home = [[HomeVC alloc] init];
    UINavigationController *nav = [[UINavigationController alloc] initWithRootViewController:home];
    self.window.rootViewController = nav;
    [self.window makeKeyAndVisible];
    return YES;
}

- (void)applicationDidBecomeActive:(UIApplication *)app {
    gwLog(@"app ra trước");
}

@end

int main(int argc, char *argv[]) {
    @autoreleasepool {
        return UIApplicationMain(argc, argv, nil, NSStringFromClass([AppDelegate class]));
    }
}
